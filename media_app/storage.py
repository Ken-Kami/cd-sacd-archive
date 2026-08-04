from __future__ import annotations

import csv
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Iterator

import requests

from media_app.models import AlbumDraft, TrackDraft, join_people


DATA_FIELDS = (
    "id", "title", "title_original", "artists", "composers", "performers",
    "label", "catalog_number", "barcode", "media_type", "disc_count", "origin",
    "country", "release_year", "genre", "recording_format", "location",
    "purchase_date", "purchase_price", "condition", "notes", "musicbrainz_release_id",
    "source", "created_at",
)
TEXT_FIELDS = tuple(field for field in DATA_FIELDS if field not in {"id", "disc_count", "purchase_price"})


class StorageUnavailableError(RuntimeError):
    """Supabaseへ接続できない場合のエラー。"""


def using_supabase() -> bool:
    return bool(os.getenv("SUPABASE_URL", "").strip() and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip())


def storage_description() -> str:
    return "Supabase (PostgreSQL)" if using_supabase() else str(database_path())


def _supabase_request(method: str, table: str, *, params=None, json=None, prefer="", headers=None):
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    request_headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        request_headers["Prefer"] = prefer
    if headers:
        request_headers.update(headers)
    try:
        response = requests.request(method, f"{url}/rest/v1/{table}", params=params, json=json, headers=request_headers, timeout=30)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise StorageUnavailableError(f"Supabaseの{table}テーブルへ接続できません。") from exc


def _supabase_all(table: str, params: dict | None = None) -> list[dict]:
    rows: list[dict] = []
    page_size = 1000
    for start in range(0, 1000000, page_size):
        page = _supabase_request("GET", table, params=params, headers={"Range": f"{start}-{start + page_size - 1}"}).json()
        rows.extend(page)
        if len(page) < page_size:
            break
    return rows


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
    if path is None and using_supabase():
        _supabase_request("GET", "albums", params={"select": "id", "limit": "1"})
        _supabase_request("GET", "tracks", params={"select": "id", "limit": "1"})
        return
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
    if path is None and using_supabase():
        rows = _supabase_request("POST", "albums", json=record, prefer="return=representation").json()
        return int(rows[0]["id"])
    with connect(path) as db:
        cursor = db.execute(
            f"INSERT INTO albums ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            [record.get(field) for field in fields],
        )
        return int(cursor.lastrowid)


def update_album(album_id: int, album: AlbumDraft, path: Path | None = None) -> None:
    record = _record(album, "edited")
    fields = [field for field in DATA_FIELDS if field not in {"id", "created_at", "source"}]
    if path is None and using_supabase():
        _supabase_request("PATCH", "albums", params={"id": f"eq.{album_id}"}, json={field: record.get(field) for field in fields}, prefer="return=minimal")
        return
    with connect(path) as db:
        db.execute(
            f"UPDATE albums SET {', '.join(f'{field} = ?' for field in fields)} WHERE id = ?",
            [record.get(field) for field in fields] + [album_id],
        )


def list_albums(query: str = "", path: Path | None = None) -> list[dict]:
    if path is None and using_supabase():
        rows = _supabase_all("albums", {"select": "*", "order": "id.desc"})
    else:
        with connect(path) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM albums ORDER BY id DESC")]
    needle = query.strip().casefold()
    if not needle:
        return rows
    searchable = ("title", "title_original", "artists", "composers", "performers", "label", "catalog_number", "barcode", "genre", "location", "notes")
    return [row for row in rows if any(needle in str(row.get(field, "")).casefold() for field in searchable)]


def delete_album(album_id: int, path: Path | None = None) -> None:
    if path is None and using_supabase():
        _supabase_request("DELETE", "albums", params={"id": f"eq.{album_id}"}, prefer="return=minimal")
        return
    with connect(path) as db:
        db.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
        db.execute("DELETE FROM albums WHERE id = ?", (album_id,))


def get_album(album_id: int, path: Path | None = None) -> dict | None:
    if path is None and using_supabase():
        rows = _supabase_request("GET", "albums", params={"select": "*", "id": f"eq.{album_id}", "limit": "1"}).json()
        return rows[0] if rows else None
    with connect(path) as db:
        row = db.execute("SELECT * FROM albums WHERE id = ?", (album_id,)).fetchone()
        return dict(row) if row else None


def replace_tracks(album_id: int, tracks: list[TrackDraft], path: Path | None = None) -> None:
    if path is None and using_supabase():
        _supabase_request("DELETE", "tracks", params={"album_id": f"eq.{album_id}"}, prefer="return=minimal")
        records = [dict(item.model_dump(), album_id=album_id) for item in tracks]
        for start in range(0, len(records), 500):
            _supabase_request("POST", "tracks", json=records[start:start + 500], prefer="return=minimal")
        return
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
    if path is None and using_supabase():
        return _supabase_all("tracks", {"select": "*", "album_id": f"eq.{album_id}", "order": "disc_number.asc,id.asc"})
    with connect(path) as db:
        return [
            dict(row) for row in db.execute(
                "SELECT * FROM tracks WHERE album_id = ? ORDER BY disc_number, id",
                (album_id,),
            )
        ]


def list_all_tracks(path: Path | None = None) -> list[dict]:
    if path is None and using_supabase():
        albums = {int(row["id"]): row for row in _supabase_all("albums", {"select": "*", "order": "id.asc"})}
        tracks = _supabase_all("tracks", {"select": "*", "order": "album_id.asc,disc_number.asc,id.asc"})
        result = []
        for track in tracks:
            album = albums.get(int(track["album_id"]), {})
            result.append(dict(track, album_title=album.get("title", ""), album_artists=album.get("artists", ""), label=album.get("label", ""), catalog_number=album.get("catalog_number", ""), barcode=album.get("barcode", ""), media_type=album.get("media_type", ""), release_year=album.get("release_year", ""), track_title=track.get("title", ""), track_artists=track.get("artists", "")))
        return result
    with connect(path) as db:
        return [
            dict(row) for row in db.execute(
                """SELECT
                    albums.id AS album_id,
                    albums.title AS album_title,
                    albums.artists AS album_artists,
                    albums.label,
                    albums.catalog_number,
                    albums.barcode,
                    albums.media_type,
                    albums.release_year,
                    tracks.disc_number,
                    tracks.track_number,
                    tracks.title AS track_title,
                    tracks.artists AS track_artists,
                    tracks.performers,
                    tracks.composers,
                    tracks.duration_ms,
                    tracks.isrc
                FROM tracks
                JOIN albums ON albums.id = tracks.album_id
                ORDER BY albums.id, tracks.disc_number, tracks.id"""
            )
        ]


def export_tracks_csv(rows: list[dict]) -> str:
    fields = (
        "album_id", "album_title", "album_artists", "label", "catalog_number",
        "barcode", "media_type", "release_year", "disc_number", "track_number",
        "track_title", "track_artists", "performers", "composers", "duration",
        "duration_ms", "isrc",
    )
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        record = dict(row)
        milliseconds = record.get("duration_ms")
        if milliseconds:
            seconds = round(int(milliseconds) / 1000)
            record["duration"] = f"{seconds // 60}:{seconds % 60:02d}"
        else:
            record["duration"] = ""
        writer.writerow(record)
    return output.getvalue()


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
    if path is None and using_supabase():
        rows = []
        if barcode.strip():
            rows.extend(_supabase_request("GET", "albums", params={"select": "*", "barcode": f"eq.{barcode.strip()}"}).json())
        if catalog_number.strip():
            rows.extend(_supabase_request("GET", "albums", params={"select": "*", "catalog_number": f"ilike.{catalog_number.strip()}"}).json())
        return list({int(row["id"]): row for row in rows}.values())
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
