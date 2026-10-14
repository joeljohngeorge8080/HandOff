"""COPY gesture, core side (ADR-057): clipboard parsing and the grab -> release -> send bridge."""

import threading

import pytest

from handoff import config
from handoff.cv.clipboard import (
    decode_selection_bytes,
    is_png,
    parse_gnome_copied,
    parse_uri_list,
)
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


PEER = "11111111-1111-4111-8111-111111111111"


def config_max():
    return config.MAX_FILE_SIZE


def make(files=("/data/a.png",), send_error=None, read_error=None, claim_error=None, active=PEER):
    bus, seen, sent, claims, clock = EventBus(), [], [], [], Clock()
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

    def claim():
        if claim_error:
            raise claim_error
        claims.append(1)

    bridge = CopyBridge(bus, send, claim, lambda: active, read, clock)
    return bridge, seen, sent, claims, clock


def gestures(seen):
    return [d["gesture"] for n, d in seen if n == "cv.event"]


# --- the laptop that grabbed ---


def test_an_open_palm_on_the_grabbing_laptop_cancels_the_grab_and_sends_nothing():
    b, seen, sent, claims, _ = make()
    b.on_grab()
    b.on_release()
    assert sent == [] and claims == []
    assert gestures(seen) == ["copied", "copy_cancelled"]
    with pytest.raises(HandOffError) as e:  # the grab is gone: a claim finds nothing
        b.serve_claim(PEER)
    assert e.value.code == "NOTHING_HELD"


def test_grab_with_nothing_on_the_clipboard_reports_it_and_holds_nothing():
    b, seen, _, _, _ = make(files=())
    b.on_grab()
    assert gestures(seen) == ["copy_empty"]
    with pytest.raises(HandOffError):
        b.serve_claim(PEER)


def test_an_unreadable_clipboard_is_reported_not_raised():
    b, seen, _, _, _ = make(read_error=HandOffError("CLIPBOARD_UNAVAILABLE", "no clipboard"))
    b.on_grab()
    assert gestures(seen) == ["copy_failed"]


def test_a_new_grab_replaces_the_previous_one():
    files = [["/data/a.png"], ["/data/b.png"]]
    b, _, sent, _, _ = make()
    b._read = lambda: files.pop(0)
    b.on_grab()
    b.on_grab()
    b.serve_claim(PEER)
    assert sent == [["/data/b.png"]]


# --- the laptop the closed hand is carried to ---


def test_an_open_palm_with_nothing_held_here_claims_from_the_connected_peer():
    b, seen, sent, claims, _ = make()
    b.on_release()
    assert claims == [1] and sent == []  # this laptop never sends on its own release
    assert gestures(seen) == ["claimed"]


def test_a_refused_claim_is_reported_not_raised():
    err = HandOffError("NOTHING_HELD", "The other device is not holding anything.")
    b, seen, _, _, _ = make(claim_error=err)
    b.on_release()
    assert gestures(seen) == ["claim_failed"]


def test_an_unexpected_error_while_claiming_is_contained():
    b, seen, _, _, _ = make(claim_error=RuntimeError("boom"))
    b.on_release()
    assert gestures(seen) == ["claim_failed"]


def test_a_stale_grab_does_not_cancel_a_release_it_claims_instead():
    b, seen, _, claims, clock = make()
    b.on_grab()
    clock.now += config.CV_HOLD_MAX_SECONDS + 1
    b.on_release()
    assert claims == [1]


# --- answering a claim (the peer endpoint calls this) ---


def test_a_claim_from_the_connected_peer_sends_the_held_files_once():
    b, seen, sent, _, _ = make()
    b.on_grab()
    out = b.serve_claim(PEER)
    assert out == {"transfer_id": "t1"} and sent == [["/data/a.png"]]
    assert gestures(seen) == ["copied", "sent"]
    with pytest.raises(HandOffError) as e:  # one grab sends once
        b.serve_claim(PEER)
    assert e.value.code == "NOTHING_HELD"
    assert len(sent) == 1


def test_a_claim_with_nothing_held_is_refused():
    b, _, sent, _, _ = make()
    with pytest.raises(HandOffError) as e:
        b.serve_claim(PEER)
    assert e.value.code == "NOTHING_HELD" and sent == []


def test_a_claim_after_the_grab_expired_is_refused():
    b, _, sent, _, clock = make()
    b.on_grab()
    clock.now += config.CV_HOLD_MAX_SECONDS + 1
    with pytest.raises(HandOffError) as e:
        b.serve_claim(PEER)
    assert e.value.code == "NOTHING_HELD" and sent == []


def test_only_the_connected_peer_may_claim_and_a_wrong_claimer_does_not_use_up_the_grab():
    b, _, sent, _, _ = make()
    b.on_grab()
    other = "22222222-2222-4222-8222-222222222222"
    with pytest.raises(HandOffError) as e:
        b.serve_claim(other)
    assert e.value.code == "DEVICE_NOT_FOUND" and sent == []
    assert b.serve_claim(PEER) == {"transfer_id": "t1"}


def test_a_claim_with_no_connected_peer_is_refused():
    b, _, sent, _, _ = make(active=None)
    b.on_grab()
    with pytest.raises(HandOffError) as e:
        b.serve_claim(PEER)
    assert e.value.code == "DEVICE_NOT_FOUND" and sent == []


def test_a_rejected_send_is_reported_to_the_claimer_and_the_grab_is_used_up():
    err = HandOffError("FILE_TYPE_NOT_SUPPORTED", "nope")
    b, seen, _, _, _ = make(send_error=err)
    b.on_grab()
    with pytest.raises(HandOffError) as e:
        b.serve_claim(PEER)
    assert e.value.code == "FILE_TYPE_NOT_SUPPORTED"
    assert gestures(seen) == ["copied", "send_failed"]
    with pytest.raises(HandOffError):
        b.serve_claim(PEER)


def test_an_unexpected_error_in_send_becomes_a_generic_failure_not_a_crash():
    b, seen, _, _, _ = make(send_error=RuntimeError("boom"))
    b.on_grab()
    with pytest.raises(HandOffError) as e:
        b.serve_claim(PEER)
    assert e.value.code == "INTERNAL_ERROR"
    assert gestures(seen) == ["copied", "send_failed"]


def test_a_claim_waits_for_a_grab_that_is_still_reading_the_clipboard():
    started, finish = threading.Event(), threading.Event()
    b, _, sent, _, _ = make()

    def slow_read():
        started.set()
        finish.wait(2)
        return ["/data/a.png"]

    b._read = slow_read
    g = threading.Thread(target=b.on_grab)
    g.start()
    started.wait(2)
    result = []
    c = threading.Thread(target=lambda: result.append(b.serve_claim(PEER)))
    c.start()
    finish.set()
    g.join(3)
    c.join(3)
    assert sent == [["/data/a.png"]] and result == [{"transfer_id": "t1"}]


def test_tk_hex_byte_dump_of_a_uri_list_is_decoded_regression():
    # Tk returns custom clipboard formats as hex bytes; it once made every grab look empty
    from handoff.cv.clipboard import decode_selection

    raw = "file:///data/My%20Pic.png\r\n"
    dump = " ".join(f"0x{b:x}" for b in raw.encode())
    assert parse_uri_list(decode_selection(dump)) == ["/data/My Pic.png"]
    assert decode_selection("plain text") == "plain text"
    assert decode_selection("0xzz 0x1") == "0xzz 0x1"
    assert decode_selection("0xff 0xfe") == ""  # not UTF-8: nothing usable


# ----- clipboard images (a picture copied from a browser) --------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_png_is_recognised_by_its_signature_only():
    assert is_png(PNG)
    for bad in (
        b"",
        b"\x89PNG",
        b"GIF89a....",
        b"MZ" + b"\x00" * 40,
        b"\xff\xd8\xff\xe0" + b"\x00" * 20,
    ):
        assert not is_png(bad)


def test_tk_hex_dump_decodes_to_the_raw_bytes_regression():
    dump = " ".join(f"0x{b:x}" for b in PNG)
    assert decode_selection_bytes(dump) == PNG
    assert decode_selection_bytes("not hex") is None
    assert decode_selection_bytes("0x1 0xzz") is None
    assert decode_selection_bytes("") is None


# ----- what is held, shown, stashed and cleaned up ---------------------------------------


class Sched:
    """A scheduler that never fires by itself; tests call fire()."""

    def __init__(self) -> None:
        self.jobs: list[list] = []

    def __call__(self, delay, fn):
        job = [delay, fn, False]
        self.jobs.append(job)

        def cancel():
            job[2] = True

        return cancel

    def fire(self):
        for _delay, fn, cancelled in list(self.jobs):
            if not cancelled:
                fn()


def make_held(files=("/data/a.png",), image=None):
    bus, seen, sent, stashed, discarded, sched = EventBus(), [], [], [], [], Sched()
    bus.subscribe(lambda n, d: seen.append((n, d)))
    clock = Clock()

    def stash(data):
        path = f"/data/held/image-{len(stashed) + 1}.png"
        stashed.append((path, data))
        return path

    b = CopyBridge(
        bus,
        lambda paths: sent.append(paths) or {"transfer": {"transfer_id": "t1"}},
        lambda: None,
        lambda: PEER,
        lambda: list(files),
        clock,
        read_image=lambda: image,
        stash=stash,
        discard=lambda p: discarded.append(p),
        schedule=sched,
    )
    return b, seen, sent, stashed, discarded, sched


def held_events(seen):
    return [d for n, d in seen if n == "hand.held"]


def test_a_grab_tells_the_ui_what_is_held_by_name_and_count():
    b, seen, *_ = make_held(files=("/home/u/Pictures/cat.png", "C:\\Users\\u\\notes.txt"))
    b.on_grab()
    assert held_events(seen) == [{"names": ["cat.png", "notes.txt"], "count": 2}]


def test_names_are_sanitised_and_bounded_before_they_reach_the_ui():
    nasty = "/x/" + "a" * 500 + ".png"
    many = [f"/x/f{i}.png" for i in range(60)]
    b, seen, *_ = make_held(files=[nasty, "/x/line\nbreak\x00.png", *many])
    b.on_grab()
    (event,) = held_events(seen)
    assert event["count"] == 62
    assert len(event["names"]) <= 20
    assert all(len(n) <= 120 and "\n" not in n and "\x00" not in n for n in event["names"])


def test_cancelling_sending_or_replacing_a_grab_clears_what_the_ui_shows():
    b, seen, *_ = make_held()
    b.on_grab()
    b.on_release()  # cancel
    assert held_events(seen)[-1] == {"names": [], "count": 0}

    b, seen, *_ = make_held()
    b.on_grab()
    b.serve_claim(PEER)  # sent
    assert held_events(seen)[-1] == {"names": [], "count": 0}


def test_nothing_to_clear_means_no_clearing_event():
    b, seen, *_ = make_held(files=())
    b.on_grab()  # empty clipboard, nothing was held before
    assert held_events(seen) == []


def test_an_empty_grab_after_a_held_one_clears_the_display():
    files = [["/data/a.png"], []]
    b, seen, *_ = make_held()
    b._read = lambda: files.pop(0)
    b.on_grab()
    b.on_grab()
    assert held_events(seen)[-1] == {"names": [], "count": 0}


def test_an_unclaimed_grab_expires_and_the_display_clears():
    b, seen, sent, _, _, sched = make_held()
    b.on_grab()
    assert sched.jobs[-1][0] == config.CV_HOLD_MAX_SECONDS
    sched.fire()
    assert held_events(seen)[-1] == {"names": [], "count": 0}
    with pytest.raises(HandOffError):
        b.serve_claim(PEER)


def test_an_old_expiry_cannot_clear_a_newer_grab():
    b, seen, sent, _, _, sched = make_held()
    b.on_grab()
    stale = sched.jobs[0][1]
    b.on_grab()  # replaces it
    stale()  # the first grab's timer fires late anyway
    assert held_events(seen)[-1] == {"names": ["a.png"], "count": 1}  # still shown
    assert b.serve_claim(PEER) == {"transfer_id": "t1"}  # still claimable


def test_a_picture_on_the_clipboard_becomes_a_held_png_when_there_are_no_files():
    b, seen, sent, stashed, discarded, _ = make_held(files=(), image=PNG)
    b.on_grab()
    assert stashed == [("/data/held/image-1.png", PNG)]
    assert gestures(seen) == ["copied"]
    assert held_events(seen) == [{"names": ["image-1.png"], "count": 1}]
    b.serve_claim(PEER)
    assert sent == [["/data/held/image-1.png"]]
    assert discarded == ["/data/held/image-1.png"]  # the temporary copy is removed after the send


def test_files_win_over_a_picture_and_real_files_are_never_deleted():
    b, _, sent, stashed, discarded, _ = make_held(files=("/data/a.png",), image=PNG)
    b.on_grab()
    b.serve_claim(PEER)
    assert stashed == [] and sent == [["/data/a.png"]] and discarded == []


def test_something_that_is_not_a_png_is_treated_as_nothing_copied():
    b, seen, _, stashed, _, _ = make_held(files=(), image=b"MZ" + b"\x00" * 64)
    b.on_grab()
    assert stashed == [] and gestures(seen) == ["copy_empty"]


def test_an_oversized_picture_is_refused_before_it_is_written():
    big = PNG + b"\x00" * (config_max() + 1)
    b, seen, _, stashed, _, _ = make_held(files=(), image=big)
    b.on_grab()
    assert stashed == [] and gestures(seen) == ["copy_failed"]


def test_padding_after_the_png_end_is_trimmed_before_it_is_stored():
    padded = PNG + b"IEND\xaeB`\x82" + b"\x00" * 100
    b, _, _, stashed, _, _ = make_held(files=(), image=padded)
    b.on_grab()
    assert stashed[0][1].endswith(b"IEND\xaeB`\x82")


def test_the_temporary_picture_is_removed_when_the_grab_is_cancelled_replaced_or_expires():
    b, _, _, _, discarded, sched = make_held(files=(), image=PNG)
    b.on_grab()
    b.on_release()
    assert discarded == ["/data/held/image-1.png"]
    b.on_grab()
    b.on_grab()  # replaced: the first copy goes
    assert discarded[-1] == "/data/held/image-2.png"
    sched.fire()
    assert discarded[-1] == "/data/held/image-3.png"


def test_the_temporary_picture_is_removed_even_when_the_send_is_refused():
    b, _, _, _, discarded, _ = make_held(files=(), image=PNG)
    b._send = lambda p: (_ for _ in ()).throw(HandOffError("FILE_TYPE_NOT_SUPPORTED", "x"))
    b.on_grab()
    with pytest.raises(HandOffError):
        b.serve_claim(PEER)
    assert discarded == ["/data/held/image-1.png"]
