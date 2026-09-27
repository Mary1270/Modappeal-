from stub_genlayer import gl, Address, u256, commitment_hash, assert_raises, set_time
from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round
from modappeal import JUROR_STAKE, COMMIT_WINDOW_SECONDS, REVEAL_WINDOW_SECONDS
from test_escalation import split_no_majority


def test_mismatched_reveal_rejected():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")
    jurors = c.get_round(case_id, 1)["jurors"]

    set_time(2000)
    for addr in jurors:
        gl.message.sender_address = Address(addr)
        gl.message.value = JUROR_STAKE
        c.commit_vote(case_id, 1, commitment_hash("VIOLATION", f"salt-{addr}"))
    gl.message.value = u256(0)

    set_time(2000 + COMMIT_WINDOW_SECONDS + 1)
    c.close_commit(case_id, 1)

    gl.message.sender_address = Address(jurors[0])
    with assert_raises():
        c.reveal_vote(case_id, 1, "NO_VIOLATION", f"salt-{jurors[0]}")  # verdict doesn't match commitment


def test_double_reveal_rejected():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")
    jurors = c.get_round(case_id, 1)["jurors"]
    votes = {addr: "VIOLATION" for addr in jurors}
    run_round(c, case_id, 1, votes, start_time=2000)
    # round 1 already tallied and moved on; can't reveal again on round 1
    gl.message.sender_address = Address(jurors[0])
    with assert_raises():
        c.reveal_vote(case_id, 1, "VIOLATION", f"salt-{jurors[0]}")


def test_stale_round_reveal_rejected_after_escalation():
    """A round-1 juror must not be able to reveal into round 1 once the case
    has moved on to round 2 -- round_id is a hard scope, not decoration."""
    c = new_contract()
    all_jurors = [f"0xj{i}" for i in range(30)]
    register_jurors(c, all_jurors)
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")

    round1_jurors = c.get_round(case_id, 1)["jurors"]
    # split three ways -> no majority -> escalates to round 2
    votes = split_no_majority(round1_jurors)

    commit_time = 2000
    set_time(commit_time)
    salts = {addr: f"salt-{addr}" for addr in votes}
    for addr, verdict in votes.items():
        gl.message.sender_address = Address(addr)
        gl.message.value = JUROR_STAKE
        c.commit_vote(case_id, 1, commitment_hash(verdict, salts[addr]))
    gl.message.value = u256(0)
    set_time(commit_time + COMMIT_WINDOW_SECONDS + 1)
    c.close_commit(case_id, 1)
    for addr, verdict in votes.items():
        gl.message.sender_address = Address(addr)
        c.reveal_vote(case_id, 1, verdict, salts[addr])
    set_time(commit_time + COMMIT_WINDOW_SECONDS + REVEAL_WINDOW_SECONDS + 2)
    c.tally_round(case_id, 1)

    assert c.get_case(case_id)["current_round_id"] == 2

    # now try to reveal late into round 1 using a juror who committed but the
    # round has already moved to round 2
    late_juror = round1_jurors[0]
    gl.message.sender_address = Address(late_juror)
    with assert_raises():
        c.reveal_vote(case_id, 1, votes[late_juror], salts[late_juror])
