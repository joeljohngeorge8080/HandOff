import pytest

from handoff.errors import HandOffError
from handoff.transfer.states import (
    TERMINAL_STATUSES,
    FileStatus,
    TransferStatus,
    assert_transition,
    can_transition,
    derive_job_status,
)

S = TransferStatus


def test_happy_path_is_allowed():
    path = [S.CREATED, S.VALIDATING, S.ACCEPTED, S.TRANSFERRING, S.COMPLETED]
    for a, b in zip(path, path[1:], strict=False):
        assert can_transition(a, b)


@pytest.mark.parametrize("src", [S.CREATED, S.VALIDATING, S.ACCEPTED, S.TRANSFERRING])
def test_any_active_state_can_fail(src):
    assert can_transition(src, S.FAILED)


def test_transferring_can_become_partially_completed():
    assert can_transition(S.TRANSFERRING, S.PARTIALLY_COMPLETED)


@pytest.mark.parametrize("terminal", [S.COMPLETED, S.FAILED, S.PARTIALLY_COMPLETED])
def test_terminal_states_have_no_exit(terminal):
    assert terminal in TERMINAL_STATUSES
    for dst in S:
        assert not can_transition(terminal, dst)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (S.COMPLETED, S.TRANSFERRING),
        (S.CREATED, S.COMPLETED),
        (S.CREATED, S.TRANSFERRING),
        (S.VALIDATING, S.COMPLETED),
        (S.ACCEPTED, S.COMPLETED),
        (S.ACCEPTED, S.PARTIALLY_COMPLETED),
        (S.TRANSFERRING, S.ACCEPTED),
        (S.CREATED, S.CREATED),
    ],
)
def test_invalid_transitions_are_rejected(src, dst):
    assert not can_transition(src, dst)
    with pytest.raises(HandOffError) as e:
        assert_transition(src, dst)
    assert e.value.code == "INVALID_STATE"


def test_there_is_no_cancelled_state():
    assert "cancelled" not in {s.value for s in S}


def test_status_values_match_api_contract():
    assert {s.value for s in S} == {
        "created",
        "validating",
        "accepted",
        "transferring",
        "completed",
        "failed",
        "partially_completed",
    }
    assert {s.value for s in FileStatus} == {"pending", "transferring", "completed", "failed"}


def test_job_status_all_completed():
    assert derive_job_status([FileStatus.COMPLETED] * 3) == S.COMPLETED


def test_job_status_all_failed():
    assert derive_job_status([FileStatus.FAILED] * 2) == S.FAILED


def test_job_status_mixed_is_partial():
    result = derive_job_status([FileStatus.COMPLETED, FileStatus.COMPLETED, FileStatus.FAILED])
    assert result == S.PARTIALLY_COMPLETED


@pytest.mark.parametrize("unfinished", [FileStatus.PENDING, FileStatus.TRANSFERRING])
def test_job_status_requires_every_file_to_be_finished(unfinished):
    with pytest.raises(HandOffError) as e:
        derive_job_status([FileStatus.COMPLETED, unfinished])
    assert e.value.code == "INVALID_STATE"


def test_job_status_of_empty_job_is_invalid():
    with pytest.raises(HandOffError):
        derive_job_status([])
