from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


MEDIA_TYPES = ["CD", "SACD", "SACD Hybrid", "Blu-ray Audio", "その他"]
ORIGINS = ["国内盤", "輸入盤", "不明"]
GENRES = ["クラシック", "ジャズ", "ロック／ポップス", "邦楽", "映画音楽", "その他"]


class AlbumDraft(BaseModel):
    title: str = ""
    title_original: str = ""
    artists: list[str] = Field(default_factory=list)
    composers: list[str] = Field(default_factory=list)
    performers: list[str] = Field(default_factory=list)
    label: str = ""
    catalog_number: str = ""
    barcode: str = ""
    media_type: str = "CD"
    disc_count: int = Field(default=1, ge=1)
    origin: str = "不明"
    country: str = ""
    release_year: str = ""
    genre: str = ""
    recording_format: str = ""
    location: str = ""
    purchase_date: str = ""
    purchase_price: Optional[int] = Field(default=None, ge=0)
    condition: str = ""
    notes: str = ""
    musicbrainz_release_id: str = ""
    cover_url: str = ""
    cover_source: str = ""


class TrackDraft(BaseModel):
    disc_number: int = Field(default=1, ge=1)
    track_number: str = ""
    title: str = ""
    artists: str = ""
    performers: str = ""
    composers: str = ""
    duration_ms: Optional[int] = Field(default=None, ge=0)
    isrc: str = ""


class AlbumLookupResult(BaseModel):
    """外部検索または画像認識で得たアルバム情報一式。"""

    album: AlbumDraft = Field(default_factory=AlbumDraft)
    tracks: list[TrackDraft] = Field(default_factory=list)
    source: str = ""


def split_people(value: str) -> list[str]:
    return [part.strip() for part in value.replace("；", ";").split(";") if part.strip()]


def join_people(values: list[str]) -> str:
    return " ; ".join(value.strip() for value in values if value.strip())
