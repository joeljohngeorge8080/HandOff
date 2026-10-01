import copy

import pytest

from handoff.config import MAX_FILE_SIZE, MAX_FILES_PER_TRANSFER
from handoff.db.models import File, utcnow
from handoff.errors import HandOffError
from handoff.transfer.manifest import Manifest, build_manifest

H = "a" * 64


def good(**over):
    entry = {"file_id": "file-001", "filename": "photo.jpg", "size": 123, "sha256": H}
    entry.update(over)
    return {"transfer_id": "tr_01ABC", "files": [entry]}


def test_valid_manifest_parses_and_round_trips():
    m = Manifest.from_dict(good())
    assert m.transfer_id == "tr_01ABC" and m.files[0].filename == "photo.jpg"
    assert m.total_size == 123
    assert Manifest.from_dict(m.to_dict()) == m


def test_manifest_is_built_from_stored_file_metadata_only():
    now = utcnow()
    f = File(
        id="f1", original_name="a.txt", stored_name="f1", extension=".txt", size_bytes=5,
        sha256=H, source="imported", storage_path="files/f1", created_at=now, updated_at=now,
    )  # fmt: skip
    m = build_manifest("tr_9", [f])
    assert m.files[0].sha256 == H and m.files[0].size == 5 and m.files[0].file_id == "f1"


@pytest.mark.parametrize("bad", [None, [], "x", 5, {"files": []}])
def test_non_object_or_empty_manifest_is_rejected(bad):
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(bad)
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


@pytest.mark.parametrize("tid", [None, "", "a/b", "../x", "x" * 65, 5, "a b"])
def test_bad_transfer_ids_are_rejected(tid):
    data = good()
    data["transfer_id"] = tid
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(data)
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


@pytest.mark.parametrize("sha", ["", "xyz", "A" * 64, "a" * 63, "a" * 65, None, 5])
def test_bad_hashes_are_rejected(sha):
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(good(sha256=sha))
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


@pytest.mark.parametrize("fid", [None, "", "a/b", 7])
def test_bad_file_ids_are_rejected(fid):
    with pytest.raises(HandOffError):
        Manifest.from_dict(good(file_id=fid))


@pytest.mark.parametrize("name", ["../../x.txt", "/etc/passwd", "a\\b.txt", "C:\\a.exe"])
def test_traversal_names_are_rejected_with_a_path_code(name):
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(good(filename=name))
    assert e.value.code == "INVALID_PATH"


def test_unsupported_extension_and_oversize_use_specific_codes():
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(good(filename="a.pdf"))
    assert e.value.code == "FILE_TYPE_NOT_SUPPORTED"
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(good(size=MAX_FILE_SIZE + 1))
    assert e.value.code == "FILE_TOO_LARGE"
    Manifest.from_dict(good(size=MAX_FILE_SIZE))  # exactly 50 MB is fine


@pytest.mark.parametrize("size", [True, "5", 1.5, None, -1])
def test_bad_sizes_are_rejected(size):
    with pytest.raises(HandOffError):
        Manifest.from_dict(good(size=size))


def test_duplicate_names_are_rejected_case_insensitively():
    data = good()
    dup = copy.deepcopy(data["files"][0])
    dup["file_id"], dup["filename"] = "file-002", "PHOTO.JPG"
    data["files"].append(dup)
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict(data)
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"


def test_too_many_files_are_rejected():
    entry = good()["files"][0]
    files = [
        {**entry, "file_id": f"f{i}", "filename": f"n{i}.txt"}
        for i in range(MAX_FILES_PER_TRANSFER + 1)
    ]
    with pytest.raises(HandOffError) as e:
        Manifest.from_dict({"transfer_id": "t", "files": files})
    assert e.value.code == "TRANSFER_VALIDATION_FAILED"
    Manifest.from_dict({"transfer_id": "t", "files": files[:MAX_FILES_PER_TRANSFER]})


def test_non_object_file_entry_is_rejected():
    with pytest.raises(HandOffError):
        Manifest.from_dict({"transfer_id": "t", "files": ["a.txt"]})
