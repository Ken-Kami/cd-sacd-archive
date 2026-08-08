from pathlib import Path
from io import BytesIO

import zxingcpp
from PIL import Image

from media_app.models import AlbumDraft, TrackDraft, join_people, split_people
from media_app import recognition, storage
from media_app.recognition import barcode_from_image, barcode_is_valid, normalize_barcode
from media_app.storage import add_album, delete_album, duplicate_candidates, export_tracks_csv, initialize, list_albums, list_all_tracks, list_tracks, replace_tracks, update_album, update_track_ratings


def test_barcode_helpers() -> None:
    assert normalize_barcode("4 988006-700802") == "4988006700802"
    assert barcode_is_valid("4988006700802")
    assert not barcode_is_valid("4988006700803")


def test_barcode_from_photo_like_image() -> None:
    barcode = zxingcpp.create_barcode("4988006700802", zxingcpp.BarcodeFormat.EAN13)
    generated = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, 500, True, True))
    canvas = Image.new("RGB", (1200, 900), "#dedbd2")
    canvas.paste(generated.convert("RGB"), (300, 280))
    buffer = BytesIO()
    canvas.save(buffer, format="JPEG", quality=82)
    assert barcode_from_image(buffer.getvalue()) == "4988006700802"


def test_people_helpers() -> None:
    assert split_people("A ; B；C") == ["A", "B", "C"]
    assert join_people(["A", "B"]) == "A ; B"


def test_storage_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "collection.db"
    initialize(path)
    album_id = add_album(AlbumDraft(title="交響曲集", artists=["管弦楽団"], catalog_number="ABC-1", disc_count=2, rating=5), path=path)
    assert list_albums("管弦楽団", path)[0]["disc_count"] == 2
    assert list_albums("管弦楽団", path)[0]["rating"] == 5
    assert len(duplicate_candidates(catalog_number="abc-1", path=path)) == 1
    update_album(album_id, AlbumDraft(title="交響曲全集", media_type="SACD"), path)
    assert list_albums("全集", path)[0]["media_type"] == "SACD"
    delete_album(album_id, path)
    assert list_albums(path=path) == []


def test_tracks_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "collection.db"
    initialize(path)
    album_id = add_album(AlbumDraft(title="曲目テスト"), path=path)
    replace_tracks(
        album_id,
        [TrackDraft(track_number="1", title="第1楽章", performers="演奏者", duration_ms=125000)],
        path,
    )
    tracks = list_tracks(album_id, path)
    assert tracks[0]["title"] == "第1楽章"
    assert tracks[0]["duration_ms"] == 125000
    assert tracks[0]["rating"] == 0
    update_track_ratings({tracks[0]["id"]: 5}, path)
    assert list_tracks(album_id, path)[0]["rating"] == 5
    all_tracks = list_all_tracks(path)
    assert all_tracks[0]["album_title"] == "曲目テスト"
    exported = export_tracks_csv(all_tracks)
    assert "track_title" in exported
    assert "第1楽章" in exported
    assert "2:05" in exported
    assert "rating" in exported


def test_supabase_album_and_tracks(monkeypatch) -> None:
    albums, tracks = [], []

    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    def fake_request(method, url, **kwargs):
        table = url.rsplit("/", 1)[-1]
        data = albums if table == "albums" else tracks
        params = kwargs.get("params") or {}
        if method == "POST":
            incoming = kwargs.get("json")
            records = incoming if isinstance(incoming, list) else [incoming]
            for record in records:
                saved = dict(record, id=len(data) + 1)
                data.append(saved)
            return Response(data[-len(records):])
        if method == "DELETE":
            album_filter = params.get("album_id", "")
            if album_filter:
                target = int(album_filter.split(".")[-1])
                data[:] = [row for row in data if int(row.get("album_id", 0)) != target]
            return Response([])
        if params.get("id"):
            target = int(params["id"].split(".")[-1])
            return Response([row for row in data if int(row["id"]) == target])
        if params.get("album_id"):
            target = int(params["album_id"].split(".")[-1])
            return Response([row for row in data if int(row["album_id"]) == target])
        return Response(list(data))

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(storage.requests, "request", fake_request)
    album_id = storage.add_album(AlbumDraft(title="クラウド盤"))
    storage.replace_tracks(album_id, [TrackDraft(title="クラウド曲")])
    assert storage.get_album(album_id)["title"] == "クラウド盤"
    assert storage.list_tracks(album_id)[0]["title"] == "クラウド曲"


def test_cover_art_prefers_front_large_thumbnail(monkeypatch) -> None:
    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {
                "images": [
                    {"front": False, "image": "https://example/back.jpg"},
                    {"front": True, "image": "https://example/front.jpg", "thumbnails": {"large": "https://example/front-large.jpg"}},
                ]
            }
    monkeypatch.setattr(recognition.requests, "get", lambda *args, **kwargs: Response())
    assert recognition.lookup_cover_art("release-id") == "https://example/front-large.jpg"


def test_metadata_merge_preserves_saved_and_personal_fields() -> None:
    saved = AlbumDraft(
        title="手入力タイトル", catalog_number="ABC-1", location="棚A",
        purchase_date="2026-08-08", purchase_price=3000, rating=4, condition="帯あり", notes="初版",
    )
    found = AlbumDraft(
        title="検索タイトル", artists=["Artist"], label="Label", catalog_number="OTHER",
        location="上書き不可", purchase_price=1, rating=1, notes="上書き不可",
        cover_url="https://example.com/cover.jpg",
    )
    merged = recognition.merge_album_missing(saved, found)
    assert merged.title == "手入力タイトル"
    assert merged.catalog_number == "ABC-1"
    assert merged.artists == ["Artist"]
    assert merged.label == "Label"
    assert merged.location == "棚A"
    assert merged.purchase_price == 3000
    assert merged.rating == 4
    assert merged.notes == "初版"
    assert merged.cover_url == "https://example.com/cover.jpg"
