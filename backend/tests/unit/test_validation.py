import pytest

from handoff.config import MAX_FILE_SIZE
from handoff.errors import HandOffError
from handoff.files.validation import (
    looks_executable,
    normalize_extension,
    validate_extension,
    validate_filename,
    validate_size,
)

MB = 1024 * 1024


@pytest.mark.parametrize("name", ["a.txt", "a.jpg", "a.jpeg", "a.png", "a.pdf"])
def test_accepts_allowed_extensions(name):
    assert validate_extension(name) == name[name.rindex(".") :]


@pytest.mark.parametrize(
    "name", ["PHOTO.JPG", "scan.JPEG", "NOTES.TXT", "Doc.PDF", "pic.PnG", "a.JpG"]
)
def test_extension_matching_is_case_insensitive(name):
    assert validate_extension(name) == normalize_extension(name)
    assert normalize_extension(name) == name[name.rindex(".") :].lower()


@pytest.mark.parametrize(
    "name",
    [
        "a.exe",
        "A.EXE",
        "a.mp4",
        "a.docx",
        "a.zip",
        "a.py",
        "a.sh",
        "a.bat",
        "a.lnk",
        "a.desktop",
        "noext",
        "a.",
        ".txt.bak",
        "photo.jpg.exe",
    ],
)
def test_rejects_unsupported_extensions(name):
    with pytest.raises(HandOffError) as e:
        validate_extension(name)
    assert e.value.code == "FILE_TYPE_NOT_SUPPORTED"


@pytest.mark.parametrize(
    "size",
    [0, 1, 1024, 49 * MB, int(49.9 * MB), MAX_FILE_SIZE],
)
def test_accepts_sizes_up_to_and_including_the_limit(size):
    validate_size(size)


@pytest.mark.parametrize("size", [MAX_FILE_SIZE + 1, 51 * MB, 72 * MB])
def test_rejects_file_larger_than_limit(size):
    with pytest.raises(HandOffError) as e:
        validate_size(size)
    assert e.value.code == "FILE_TOO_LARGE"
    assert e.value.details["maximum_size"] == MAX_FILE_SIZE


def test_limit_is_exactly_50_mb():
    assert MAX_FILE_SIZE == 52_428_800


def test_rejects_negative_size():
    with pytest.raises(HandOffError) as e:
        validate_size(-1)
    assert e.value.code == "INVALID_FILE"


@pytest.mark.parametrize(
    "name",
    [
        "photo.jpg",
        "my photo (1).jpg",
        "résumé-é.txt",
        "日本語.txt",
        "a-b_c.txt",
        "x" * 200 + ".txt",
    ],
)
def test_accepts_ordinary_filenames(name):
    assert validate_filename(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "../../file.txt",
        "../../../secret.txt",
        "..\\..\\file.txt",
        "/etc/passwd",
        "C:\\Windows\\System32\\file.exe",
        "C:file.txt",
        "dir/file.txt",
        "dir\\file.txt",
        "..",
        ".",
        "",
        "   ",
        "bad\x00name.txt",
        "bad\nname.txt",
        "CON.txt",
        "nul.txt",
        "COM1.txt",
        "lpt9.exe",
        "trailingdot.txt.",
        "trailingspace.txt ",
        "a<b.txt",
        'a"b.txt',
        "a|b.txt",
        "a?b.txt",
        "a*b.txt",
        "x" * 300 + ".txt",
    ],
)
def test_rejects_dangerous_or_invalid_filenames(name):
    with pytest.raises(HandOffError) as e:
        validate_filename(name)
    assert e.value.code in {"INVALID_FILE", "INVALID_PATH"}


def test_rejects_non_string_filename():
    with pytest.raises(HandOffError):
        validate_filename(123)  # type: ignore[arg-type]


def test_overlong_name_is_measured_in_utf8_bytes():
    with pytest.raises(HandOffError):
        validate_filename("é" * 200 + ".txt")  # 400 bytes


@pytest.mark.parametrize(
    "head",
    [b"MZ\x90\x00", b"MZ", b"\x7fELF\x02\x01"],
)
def test_detects_windows_and_linux_executables_by_content(head):
    assert looks_executable(head)


@pytest.mark.parametrize(
    "head",
    [b"", b"M", b"hello world", b"\xff\xd8\xff\xe0", b"\x89PNG\r\n\x1a\n", b"%PDF-1.7", b"mz"],
)
def test_ordinary_content_is_not_flagged_as_executable(head):
    assert not looks_executable(head)
