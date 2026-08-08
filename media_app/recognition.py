from __future__ import annotations

import base64
import io
import os
import re
from difflib import SequenceMatcher

import requests
import zxingcpp
from openai import OpenAI
from PIL import Image, ImageFilter, ImageOps

from media_app.models import AlbumDraft, AlbumLookupResult, TrackDraft


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
    """写真内のJAN／UPC／EAN／ITFを、補正条件を変えながら読み取る。"""
    original = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    # 小さく写ったバーコードの線幅を確保する。大画像はZXing側の縮小探索に任せる。
    if max(original.size) < 1800:
        scale = 1800 / max(original.size)
        original = original.resize(
            (round(original.width * scale), round(original.height * scale)),
            Image.Resampling.LANCZOS,
        )
    gray = ImageOps.grayscale(original)
    variants = (
        original,
        gray,
        ImageOps.autocontrast(gray, cutoff=1),
        ImageOps.autocontrast(gray, cutoff=2).filter(ImageFilter.SHARPEN),
    )
    for image in variants:
        for result in zxingcpp.read_barcodes(
            image,
            try_rotate=True,
            try_downscale=True,
            return_errors=False,
        ):
            candidate = normalize_barcode(result.text)
            # ZXingは既定でチェックサム不正の結果を返さない。GTINの再検証も行う。
            if barcode_is_valid(candidate):
                return candidate
    return ""


def extract_album_package_with_ai(images: list[tuple[bytes, str]]) -> AlbumLookupResult:
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
- location、purchase_date、purchase_price、rating、condition、notesは空または0のまま
- 裏面やブックレットに曲目が写っていれば、tracksへディスク番号、曲順、曲名、演者、
  作曲者、時間を、画像で確認できる範囲だけ入力

sourceは「AI画像」としてください。読み取れない文字は無理に補完しないでください。
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
        text_format=AlbumLookupResult,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("AIが音盤情報を返しませんでした。")
    parsed.album.barcode = normalize_barcode(parsed.album.barcode)
    if parsed.album.barcode and not barcode_is_valid(parsed.album.barcode):
        parsed.album.barcode = ""
    return parsed


def extract_album_with_ai(images: list[tuple[bytes, str]]) -> AlbumDraft:
    """従来APIとの互換用。"""
    return extract_album_package_with_ai(images).album


def _album_from_mb_release(release: dict, fallback_barcode: str = "") -> AlbumDraft:
    labels = release.get("label-info", [])
    label_info = labels[0] if labels else {}
    media = release.get("media", [])
    formats = {str(item.get("format", "")) for item in media}
    media_type = "SACD Hybrid" if any("Hybrid SACD" in value for value in formats) else ("SACD" if any("SACD" in value for value in formats) else "CD")
    release_id = release.get("id", "") or ""
    cover_url = lookup_cover_art(release_id)
    return AlbumDraft(
        title=release.get("title", ""),
        artists=[credit.get("name", "") for credit in release.get("artist-credit", []) if credit.get("name")],
        label=(label_info.get("label") or {}).get("name", ""),
        catalog_number=label_info.get("catalog-number", "") or "",
        barcode=normalize_barcode(release.get("barcode") or fallback_barcode),
        media_type=media_type, disc_count=max(len(media), 1),
        country=release.get("country", "") or "", release_year=(release.get("date", "") or "")[:4],
        musicbrainz_release_id=release_id, cover_url=cover_url,
        cover_source="Cover Art Archive" if cover_url else "",
    )


def _mb_search(query: str, limit: int = 5) -> list[dict]:
    response = requests.get(
        "https://musicbrainz.org/ws/2/release/",
        params={"query": query, "fmt": "json", "limit": str(limit)},
        headers={"User-Agent": "CDArchive/1.0 (personal collection manager)"}, timeout=15,
    )
    response.raise_for_status()
    return response.json().get("releases", [])


def lookup_musicbrainz(barcode: str) -> AlbumDraft | None:
    normalized = normalize_barcode(barcode)
    candidates = [normalized]
    if len(normalized) == 12:
        candidates.append(f"0{normalized}")
    elif len(normalized) == 13 and normalized.startswith("0"):
        candidates.append(normalized[1:])
    releases = _mb_search(" OR ".join(f"barcode:{value}" for value in candidates), 1)
    if not releases:
        return None
    return _album_from_mb_release(releases[0], normalized)


def lookup_cover_art(release_id: str) -> str:
    """MusicBrainz Release IDに対応する表ジャケットの表示用URLを返す。"""
    if not release_id:
        return ""
    try:
        response = requests.get(
            f"https://coverartarchive.org/release/{release_id}",
            headers={"Accept": "application/json", "User-Agent": "CDArchive/1.0 (personal collection manager)"},
            timeout=15,
        )
        if response.status_code == 404:
            return ""
        response.raise_for_status()
        images = response.json().get("images", [])
        selected = next((item for item in images if item.get("front")), images[0] if images else None)
        if not selected:
            return ""
        thumbnails = selected.get("thumbnails") or {}
        url = thumbnails.get("large") or thumbnails.get("500") or thumbnails.get("250") or selected.get("image", "")
        return url.replace("http://", "https://", 1)
    except (requests.RequestException, ValueError):
        return ""


def _credit_names(credits: list) -> str:
    names = [item.get("name", "") for item in credits if isinstance(item, dict) and item.get("name")]
    return " ; ".join(names)


def lookup_musicbrainz_tracks(release_id: str) -> list[TrackDraft]:
    if not release_id:
        return []
    response = requests.get(
        f"https://musicbrainz.org/ws/2/release/{release_id}",
        params={
            "inc": "recordings+artist-credits+isrcs+artist-rels+recording-level-rels+work-rels+work-level-rels",
            "fmt": "json",
        },
        headers={"User-Agent": "CDArchive/1.0 (personal collection manager)"},
        timeout=20,
    )
    response.raise_for_status()
    result: list[TrackDraft] = []
    for disc_index, medium in enumerate(response.json().get("media", []), start=1):
        disc_number = int(medium.get("position") or disc_index)
        for position, track in enumerate(medium.get("tracks", []), start=1):
            recording = track.get("recording") or {}
            performers: list[str] = []
            composers: list[str] = []
            for relation in recording.get("relations", []):
                artist = (relation.get("artist") or {}).get("name", "")
                role = relation.get("type", "")
                if artist and role in {"instrument", "vocal", "performer", "conductor"}:
                    label = f"{artist} ({role})" if role else artist
                    if label not in performers:
                        performers.append(label)
                work = relation.get("work") or {}
                for work_relation in work.get("relations", []):
                    creator = (work_relation.get("artist") or {}).get("name", "")
                    creator_role = work_relation.get("type", "")
                    if creator and creator_role in {"composer", "writer", "lyricist"}:
                        label = f"{creator} ({creator_role})"
                        if label not in composers:
                            composers.append(label)
            result.append(
                TrackDraft(
                    disc_number=disc_number,
                    track_number=str(track.get("number") or position),
                    title=track.get("title") or recording.get("title", ""),
                    artists=_credit_names(track.get("artist-credit") or recording.get("artist-credit") or []),
                    performers=" ; ".join(performers),
                    composers=" ; ".join(composers),
                    duration_ms=track.get("length") or recording.get("length"),
                    isrc=" ; ".join(recording.get("isrcs") or []),
                )
            )
    return result


def _plain(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]", "", (value or "").casefold())


def _similar(left: str, right: str) -> float:
    a, b = _plain(left), _plain(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _catalog_equal(left: str, right: str) -> bool:
    return _plain(left) == _plain(right) and bool(_plain(left))


def search_musicbrainz(seed: AlbumDraft) -> AlbumLookupResult | None:
    """盤を特定できる強い条件から順にMusicBrainzを検索する。"""
    if seed.barcode and barcode_is_valid(seed.barcode):
        found = lookup_musicbrainz(seed.barcode)
        if found:
            return AlbumLookupResult(album=found, tracks=lookup_musicbrainz_tracks(found.musicbrainz_release_id), source="MusicBrainz（バーコード）")

    searches: list[tuple[str, str]] = []
    if seed.catalog_number:
        searches.append((f'catno:"{seed.catalog_number}"', "MusicBrainz（規格品番）"))
    if seed.title:
        artist = seed.artists[0] if seed.artists else ""
        query = f'release:"{seed.title}"' + (f' AND artist:"{artist}"' if artist else "")
        searches.append((query, "MusicBrainz（タイトル・アーティスト）"))
    for query, source in searches:
        for release in _mb_search(query):
            candidate = _album_from_mb_release(release)
            if seed.catalog_number:
                if not _catalog_equal(seed.catalog_number, candidate.catalog_number):
                    continue
            elif _similar(seed.title, candidate.title) < 0.82:
                continue
            if seed.artists and candidate.artists and _similar(seed.artists[0], candidate.artists[0]) < 0.55:
                continue
            return AlbumLookupResult(album=candidate, tracks=lookup_musicbrainz_tracks(candidate.musicbrainz_release_id), source=source)
    return None


def _duration_ms(value: str) -> int | None:
    try:
        parts = [int(item) for item in value.strip().split(":")]
        if len(parts) == 2:
            return (parts[0] * 60 + parts[1]) * 1000
        if len(parts) == 3:
            return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    except (TypeError, ValueError):
        pass
    return None


def search_discogs(seed: AlbumDraft) -> AlbumLookupResult | None:
    """任意設定のDiscogsトークンを使い、版を特定して曲目まで取得する。"""
    token = os.getenv("DISCOGS_USER_TOKEN", "").strip()
    if not token:
        return None
    params: dict[str, str] = {"type": "release", "per_page": "5"}
    if seed.barcode:
        params["barcode"] = seed.barcode
    elif seed.catalog_number:
        params["catno"] = seed.catalog_number
    elif seed.title:
        params["release_title"] = seed.title
        if seed.artists:
            params["artist"] = seed.artists[0]
    else:
        return None
    headers = {"Authorization": f"Discogs token={token}", "User-Agent": "CDArchive/1.0"}
    response = requests.get("https://api.discogs.com/database/search", params=params, headers=headers, timeout=15)
    response.raise_for_status()
    for item in response.json().get("results", []):
        detail_response = requests.get(f"https://api.discogs.com/releases/{item.get('id')}", headers=headers, timeout=15)
        detail_response.raise_for_status()
        detail = detail_response.json()
        identifiers = detail.get("identifiers") or []
        barcodes = [normalize_barcode(x.get("value", "")) for x in identifiers if x.get("type") == "Barcode"]
        labels = detail.get("labels") or []
        catno = (labels[0].get("catno") if labels else "") or ""
        if seed.barcode and normalize_barcode(seed.barcode) not in barcodes:
            continue
        if seed.catalog_number and not _catalog_equal(seed.catalog_number, catno):
            continue
        title = detail.get("title", "")
        if seed.title and _similar(seed.title, title) < 0.75:
            continue
        artists = [x.get("name", "").replace(" (2)", "") for x in detail.get("artists", []) if x.get("name")]
        formats = " ".join(x.get("name", "") + " " + " ".join(x.get("descriptions", [])) for x in detail.get("formats", []))
        media_type = "SACD Hybrid" if "Hybrid" in formats and "SACD" in formats else ("SACD" if "SACD" in formats else "CD")
        album = AlbumDraft(
            title=title, artists=artists, label=(labels[0].get("name") if labels else "") or "",
            catalog_number=catno, barcode=barcodes[0] if barcodes else normalize_barcode(seed.barcode),
            media_type=media_type, disc_count=max(sum(int(x.get("qty") or 1) for x in detail.get("formats", [])), 1),
            country=detail.get("country", "") or "", release_year=str(detail.get("year") or ""),
            genre=(detail.get("genres") or [""])[0],
            cover_url=detail.get("images", [{}])[0].get("uri", "") if detail.get("images") else item.get("cover_image", ""),
            cover_source="Discogs" if (detail.get("images") or item.get("cover_image")) else "",
        )
        tracks = [TrackDraft(
            disc_number=int(str(track.get("position") or "1").split("-")[0]) if "-" in str(track.get("position") or "") else 1,
            track_number=str(track.get("position") or index), title=track.get("title", ""),
            artists=" ; ".join(x.get("name", "") for x in track.get("artists", []) if x.get("name")),
            duration_ms=_duration_ms(track.get("duration", "")),
        ) for index, track in enumerate(detail.get("tracklist", []), 1) if track.get("title")]
        return AlbumLookupResult(album=album, tracks=tracks, source="Discogs")
    return None


def search_with_ai_web(seed: AlbumDraft) -> AlbumLookupResult | None:
    """公開情報を横断検索する最終フォールバック。版を断定できない結果は捨てる。"""
    if not api_key() or not (seed.barcode or seed.catalog_number or seed.title):
        return None
    prompt = f"""
CD/SACDの正確な版と収録曲をWebで調査してください。入力情報: {seed.model_dump_json()}
バーコードまたは規格品番がある場合は完全一致する版だけを採用してください。
ない場合はタイトルと主要アーティストが一致する版だけを採用してください。
公式レーベル、MusicBrainz、Discogs等の信頼できるページを照合し、推測しないでください。
各曲について確認できれば曲順、曲名、演者、作曲者、時間を返してください。
sourceには参照したサイト名を簡潔に記載してください。見つからなければ空のalbumとtracksを返してください。
"""
    response = OpenAI(api_key=api_key()).responses.parse(
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna"),
        tools=[{"type": "web_search", "search_context_size": "low"}],
        input=prompt, text_format=AlbumLookupResult,
    )
    found = response.output_parsed
    if not found or not found.album.title:
        return None
    if seed.barcode and normalize_barcode(found.album.barcode) != normalize_barcode(seed.barcode):
        return None
    if seed.catalog_number and not _catalog_equal(seed.catalog_number, found.album.catalog_number):
        return None
    if seed.title and _similar(seed.title, found.album.title) < 0.75:
        return None
    return found


def merge_album_missing(existing: AlbumDraft, found: AlbumDraft) -> AlbumDraft:
    """既存・手入力値を保持し、空欄だけを検索結果で補完する。"""
    values = existing.model_dump()
    for field in AlbumDraft.model_fields:
        current, incoming = values.get(field), getattr(found, field)
        if field in {"location", "purchase_date", "purchase_price", "rating", "condition", "notes"}:
            continue
        if field == "disc_count":
            if int(current or 1) == 1 and int(incoming or 1) > 1:
                values[field] = incoming
        elif field in {"media_type", "origin"}:
            if current in {"", "CD", "不明"} and incoming not in {"", "CD", "不明"}:
                values[field] = incoming
        elif not current and incoming:
            values[field] = incoming
    return AlbumDraft(**values)


def enrich_album_metadata(seed: AlbumDraft, image_tracks: list[TrackDraft] | None = None) -> AlbumLookupResult:
    """複数データ源を順番に試し、失敗しても入力済み情報を返す。"""
    errors: list[str] = []
    for finder in (search_musicbrainz, search_discogs, search_with_ai_web):
        try:
            found = finder(seed)
            if found:
                return AlbumLookupResult(
                    album=merge_album_missing(seed, found.album),
                    tracks=found.tracks or (image_tracks or []), source=found.source,
                )
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    return AlbumLookupResult(album=seed, tracks=image_tracks or [], source="AI画像" if image_tracks else "")
