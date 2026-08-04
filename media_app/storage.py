from __future__ import annotations

import csv
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Iterator

from media_app.models import AlbumDraft, TrackDraft, join_people


DATA_FIELDS = (
    "id", "title", "title_original", "artists", "composers", "performers",
    "label", "catalog_number", "barcode", "media_type", "disc_count", "origin",
    "country", "release_year", "genre", "recording_format", "location",
    "purchase_date", "purchase_price", "condition", "notes", "musicbrainz_release_id",
    "source", "created_at",
)
TEXT_FIELDS = tuple(field for field in DATA_FIELDS if field not in {"id", "disc_count", "purchase_price"})


def database_path() -> Path:
    configured = os.getenv("MEDIA_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "data" / "collection.db"


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    selected = path or database_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(selected)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize(path: Path | None = None) -> None:
    columns = "".join(f", {field} TEXT NOT NULL DEFAULT ''" for field in TEXT_FIELDS if field not in {"created_at", "title"})
    with connect(path) as db:
        db.execute(
            f"""CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                disc_count INTEGER NOT NULL DEFAULT 1,
                purchase_price INTEGER,
                created_at TEXT NOT NULL
                {columns}
            )"""
        )
        existing = {row[1] for row in db.execute("PRAGMA table_info(albums)")}
        if "musicbrainz_release_id" not in existing:
            db.execute("ALTER TABLE albums ADD COLUMN musicbrainz_release_id TEXT NOT NULL DEFAULT ''")
        db.execute(
            """CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                album_id INTEGER NOT NULL,
                disc_number INTEGER NOT NULL DEFAULT 1,
                track_number TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                artists TEXT NOT NULL DEFAULT '',
                performers TEXT NOT NULL DEFAULT '',
                composers TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER,
                isrc TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
            )"""
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id, disc_number, id)")
        for field in ("barcode", "catalog_number", "title", "artists", "label"):
            db.execute(f"CREATE INDEX IF NOT EXISTS idx_albums_{field} ON albums({field})")


def _record(album: AlbumDraft, source: str) -> dict:
    raw = album.model_dump()
    raw["artists"] = join_people(album.artists)
    raw["composers"] = join_people(album.composers)
    raw["performers"] = join_people(album.performers)
    raw["source"] = source
    raw["created_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    for key, value in list(raw.items()):
        if isinstance(value, str):
            raw[key] = value.strip()
    return raw


def add_album(album: AlbumDraft, source: str = "manual", path: Path | None = None) -> int:
    record = _record(album, source)
    fields = [field for field in DATA_FIELDS if field != "id"]
    with connect(path) as db:
        cursor = db.execute(
            f"INSERT INTO albums ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            [record.get(field) for field in fields],
        )
        return int(cursor.lastrowid)


def update_album(album_id: int, album: AlbumDraft, path: Path | None = None) -> None:
    record = _record(album, "edited")
    fields = [field for field in DATA_FIELDS if field not in {"id", "created_at", "source"}]
    with connect(path) as db:
        db.execute(
            f"UPDATE albums SET {', '.join(f'{field} = ?' for field in fields)} WHERE id = ?",
            [record.get(field) for field in fields] + [album_id],
        )


def list_albums(query: str = "", path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM albums ORDER BY id DESC")]
    needle = query.strip().casefold()
    if not needle:
        return rows
    searchable = ("title", "title_original", "artists", "composers", "performers", "label", "catalog_number", "barcode", "genre", "location", "notes")
    return [row for row in rows if any(needle in str(row.get(field, "")).casefold() for field in searchable)]


def delete_album(album_id: int, path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
        db.execute("DELETE FROM albums WHERE id = ?", (album_id,))


def get_album(album_id: int, path: Path | None = None) -> dict | None:
    with connect(path) as db:
        row = db.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
        return dict(row) if row else None


def replace_tracks(album_id: int, tracks: list[TrackDraft], path: Path | None = None) -> None:
    with connect(path) as db:
        db.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
        db.executemany(
            """INSERT INTO tracks
               (album_id, disc_number, track_number, title, artists, performers,
                composers, duration_ms, isrc)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (album_id, item.disc_number, item.track_number, item.title, item.artists,
                 item.performers, item.composers, item.duration_ms, item.isrc)
                for item in tracks
            ],
        )


def list_tracks(album_id: int, path: Path | None = None) -> list[dict]:
    with connect(path) as db:
        return [
            dict(row) for row in db.execute(
                "SELECT * FROM tracks WHERE album_id = ? ORDER BY disc_number, id",
                (album_id,),
            )
        ]


def duplicate_candidates(barcode: str = "", catalog_number: str = "", path: Path | None = None) -> list[dict]:
    clauses, values = [], []
    if barcode.strip():
        clauses.append("barcode = ?")
        values.append(barcode.strip())
    if catalog_number.strip():
        clauses.append("lower(catalog_number) = lower(?)")
        values.append(catalog_number.strip())
    if not clauses:
        return []
    with connect(path) as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM albums WHERE {' OR '.join(clauses)}", values)]


def export_csv(rows: list[dict]) -> str:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=DATA_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def import_csv(data: bytes, path: Path | None = None) -> tuple[int, list[str]]:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    added, errors = 0, []
    for line, row in enumerate(reader, start=2):
        try:
            if not (row.get("title") or "").strip():
                raise ValueError("titleが空です")
            album = AlbumDraft(
                **{key: value for key, value in row.items() if key in AlbumDraft.model_fields and key not in {"artists", "composers", "performers", "disc_count", "purchase_price"}},
                artists=[v.strip() for v in (row.get("artists") or "").split(";") if v.strip()],
                composers=[v.strip() for v in (row.get("composers") or "").split(";") if v.strip()],
                performers=[v.strip() for v in (row.get("performers") or "").split(";") if v.strip()],
                disc_count=int(row.get("disc_count") or 1),
                purchase_price=int(row["purchase_price"]) if row.get("purchase_price") else None,
            )
            add_album(album, "csv", path)
            added += 1
        except (ValueError, TypeError) as exc:
            errors.append(f"{line}行目: {exc}")
    return added, errors
