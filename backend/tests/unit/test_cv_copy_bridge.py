"""COPY gesture, core side (ADR-057): clipboard parsing and the grab -> release -> send bridge."""

import threading

import pytest

from handoff import config
from handoff.cv.clipboard import parse_gnome_copied, parse_uri_list
from handoff.cv.copy_bridge import CopyBridge
from handoff.errors import HandOffError
from handoff.events import EventBus

# ----- clipboard parsing: the clipboard is untrusted input ---------------------------------


def test_uri_list_yields_absolute_local_paths_and_decodes_escapes():
    text = "# comment\r\nfile:///home/u/My%20Pic.png\r\n\r\nfile://localhost/data/a.txt\r\n"
    assert parse_uri_list(text) == ["/home/u/My Pic.png", "/data/a.txt"]


@pytest.mark.parametrize(
    "line",
    [
        "http://example.com/a.png",
        "file://otherhost/share/a.png",
        "ftp://x/a.txt",
        "relative/path.txt",
        "file:///data/a%00.png",
        "",
    ],
)
def test_non_local_or_unsafe_entries_are_dropped(line):
    assert parse_uri_list(line) == []


def test_duplicates_are_removed_and_the_list_is_capped():
    assert parse_uri_list("file:///a.png\nfile:///a.png") == ["/a.png"]
    many = "\n".join(f"file:///f{i}.png" for i in range(5000))
    assert len(parse_uri_list(many)) <= config.MAX_FILES_PER_TRANSFER + 1


def test_gnome_copied_files_skips_the_copy_or_cut_header():
    assert parse_gnome_copied("copy\nfile:///data/a.png\nfile:///data/b.txt") == [
        "/data/a.png",
        "/data/b.txt",
    ]


# ----- the bridge ------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make(files=("/data/a.png",), send_error=None, read_error=None):
    bus, seen, sent, clock = EventBus(), [], [], Clock()
    bus.subscribe(lambda n, d: seen.append((n, d)))

    def read():
        if read_error:
            raise read_error
        return list(files)

    def send(paths):
        if send_error:
            raise send_error
        sent.append(paths)
        return {"transfer": {"transfer_id": "t1"}}

    return CopyBridge(bus, send, read, clock), seen, sent, clock


def gestures(seen):
    return [d["gesture"] for n, d in seen if n == "cv.event"]


def test_grab_then_release_sends_the_copied_files_once():
    b, seen, sent, _ = make()
    b.on_grab()
    b.on_release()
    b.on_release()
    assert sent == [["/data/a.png"]]
    assert gestures(seen) == ["copied", "sent"]


def test_release_without_a_grab_sends_nothing():
    b, seen, sent, _ = make()
    b.on_release()
    assert sent == [] and gestures(seen) == []


def test_grab_with_nothing_on_the_clipboard_reports_it_and_a_release_sends_nothing():
    b, seen, sent, _ = make(files=())
    b.on_grab()
    b.on_release()
    assert sent == [] and gestures(seen) == ["copy_empty"]


def test_an_unreadable_clipboard_is_reported_not_raised():
    b, seen, sent, _ = make(read_error=HandOffError("CLIPBOARD_UNAVAILABLE", "no clipboard"))
    b.on_grab()
    b.on_release()
    assert sent == [] and gestures(seen) == ["copy_failed"]


def test_a_rejected_send_such_as_no_peer_is_reported_and_forgotten():
    err = HandOffError("DEVICE_NOT_FOUND", "No HandOff device connected")
    b, seen, sent, _ = make(send_error=err)
    b.on_grab()
    b.on_release()
    b.on_release()
    assert gestures(seen) == ["copied", "send_failed"]  # not retried by a second release


def test_an_unexpected_error_in_send_is_contained():
    b, seen, _, _ = make(send_error=RuntimeError("boom"))
    b.on_grab()
    b.on_release()
    assert gestures(seen) == ["copied", "send_failed"]


def test_a_stale_grab_is_not_sent():
    b, seen, sent, clock = make()
    b.on_grab()
    clock.now += config.CV_HOLD_MAX_SECONDS + 1
    b.on_release()
    assert sent == []


def test_a_new_grab_replaces_the_previous_one():
    files = [["/data/a.png"], ["/data/b.png"]]
    b, _, sent, _ = make()
    b._read = lambda: files.pop(0)
    b.on_grab()
    b.on_grab()
    b.on_release()
    assert sent == [["/data/b.png"]]


def test_release_waits_for_a_grab_that_is_still_reading_the_clipboard():
    started, finish = threading.Event(), threading.Event()
    b, seen, sent, _ = make()

    def slow_read():
        started.set()
        finish.wait(2)
        return ["/data/a.png"]

    b._read = slow_read
    g = threading.Thread(target=b.on_grab)
    g.start()
    started.wait(2)
    r = threading.Thread(target=b.on_release)
    r.start()
    finish.set()
    g.join(3)
    r.join(3)
    assert sent == [["/data/a.png"]]


def test_tk_hex_byte_dump_of_a_uri_list_is_decoded_regression():
    # Tk returns custom clipboard formats as hex bytes; it once made every grab look empty
    from handoff.cv.clipboard import decode_selection

    raw = "file:///data/My%20Pic.png\r\n"
    dump = " ".join(f"0x{b:x}" for b in raw.encode())
    assert parse_uri_list(decode_selection(dump)) == ["/data/My Pic.png"]
    assert decode_selection("plain text") == "plain text"
    assert decode_selection("0xzz 0x1") == "0xzz 0x1"
    assert decode_selection("0xff 0xfe") == ""  # not UTF-8: nothing usable
