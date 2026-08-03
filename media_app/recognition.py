from __future__ import annotations

import base64
import io
import os
import re

import requests
import zxingcpp
from openai import OpenAI
from PIL import Image, ImageOps

from media_app.models import AlbumDraft


def api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def normalize_barcode(value: str) -> str:
    return re.sub(r"\D", "", value)


def barcode_is_valid(value: str) -> bool:
    digits = normalize_barcode(value)
    if len(digits) not in {8, 12, 13, 14}:
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(char) * (3 if (len(body) - index) % 2 else 1) for index, char in enumerate(body))
    return (10 - total % 10) % 10 == check


def barcode_from_image(image_bytes: bytes) -> str:
    """写真内のJAN／UPC／EAN／ITFバーコードを読み取る。"""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    for result in zxingcpp.read_barcodes(image):
        candidate = normalize_barcode(result.text)
        if barcode_is_valid(candidate):
            return candidate
    return ""


def extract_album_with_ai(images: list[tuple[bytes, str]]) -> AlbumDraft:
    """ジャケット、帯、裏面、盤面の写真から確認可能な音盤情報を構造化する。"""
    if not api_key():
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    if not images:
        raise RuntimeError("画像が選択されていません。")

    prompt = """
これらは同じCDまたはSACDの、表ジャケット、裏ジャケット、帯、盤面の写真です。
画像に実際に印刷されている情報だけを抽出し、推測しないでください。
日本語表記があれば優先してください。

- title: アルバム名
- title_original: 原題または併記された外国語タイトル
- artists: 主要アーティスト、オーケストラ、アンサンブル
- composers: 作曲家
- performers: 指揮者、独奏者、歌手など（担当楽器・役割も可能なら含める）
- label: レーベル名
- catalog_number: 規格品番・カタログ番号
- barcode: JAN/UPC/EANの数字だけ
- media_type: CD、SACD、SACD Hybrid、Blu-ray Audio、その他のいずれか
- disc_count: 明確に確認できるディスク枚数。不明なら1
- origin: 「国内盤」「輸入盤」「不明」のいずれか
- country、release_year、genre、recording_formatも画像から確認できる場合のみ入力
- location、purchase_date、purchase_price、condition、notesは空のまま

曲目をnotesへ転記する必要はありません。読み取れない文字は無理に補完しないでください。
"""
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    for image_bytes, mime_type in images:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{encoded}",
                "detail": "high",
            }
        )
    response = OpenAI(api_key=api_key()).responses.parse(
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna"),
        input=[{"role": "user", "content": content}],
        text_format=AlbumDraft,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("AIが音盤情報を返しませんでした。")
    parsed.barcode = normalize_barcode(parsed.barcode)
    if parsed.barcode and not barcode_is_valid(parsed.barcode):
        parsed.barcode = ""
    return parsed


def lookup_musicbrainz(barcode: str) -> AlbumDraft | None:
    normalized = normalize_barcode(barcode)
    response = requests.get(
        "https://musicbrainz.org/ws/2/release/",
        params={"query": f"barcode:{normalized}", "fmt": "json", "limit": "1"},
        headers={"User-Agent": "CDArchive/1.0 (personal collection manager)"},
        timeout=15,
    )
    response.raise_for_status()
    releases = response.json().get("releases", [])
    if not releases:
        return None
    release = releases[0]
    labels = release.get("label-info", [])
    label_info = labels[0] if labels else {}
    media = release.get("media", [])
    formats = {str(item.get("format", "")) for item in media}
    media_type = "SACD Hybrid" if any("Hybrid SACD" in value for value in formats) else ("SACD" if any("SACD" in value for value in formats) else "CD")
    return AlbumDraft(
        title=release.get("title", ""),
        artists=[credit.get("name", "") for credit in release.get("artist-credit", []) if credit.get("name")],
        label=(label_info.get("label") or {}).get("name", ""),
        catalog_number=label_info.get("catalog-number", "") or "",
        barcode=normalized,
        media_type=media_type,
        disc_count=max(len(media), 1),
        country=release.get("country", "") or "",
        release_year=(release.get("date", "") or "")[:4],
    )
