from pathlib import Path

from media_app.models import AlbumDraft, join_people, split_people
from media_app.recognition import barcode_is_valid, normalize_barcode
from media_app.storage import add_album, delete_album, duplicate_candidates, initialize, list_albums, update_album


def test_barcode_helpers() -> None:
    assert normalize_barcode("4 988006-700802") == "4988006700802"
    assert barcode_is_valid("4988006700802")
    assert not barcode_is_valid("4988006700803")


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
