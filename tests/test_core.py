from pathlib import Path
from io import BytesIO

import zxingcpp
from PIL import Image

from media_app.models import AlbumDraft, TrackDraft, join_people, split_people
from media_app.recognition import barcode_from_image, barcode_is_valid, normalize_barcode
from media_app.storage import add_album, delete_album, duplicate_candidates, initialize, list_albums, list_tracks, replace_tracks, update_album


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
    album_id = add_album(AlbumDraft(title="交響曲集", artists=["管弦楽団"], catalog_number="ABC-1", disc_count=2), path=path)
    assert list_albums("管弦楽団", path)[0]["disc_count"] == 2
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
