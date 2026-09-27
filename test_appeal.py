from stub_genlayer import gl, Address, u256, assert_raises, set_time
from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal
from modappeal import APPEAL_STAKE, APPEAL_WINDOW_SECONDS


def test_only_flagger_can_appeal_no_violation():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="NO_VIOLATION")
    with assert_raises():
        file_appeal(c, case_id, appellant="0xpub")  # wrong appellant
    file_appeal(c, case_id, appellant="0xflag")  # correct


def test_only_publisher_can_appeal_violation():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    with assert_raises():
        file_appeal(c, case_id, appellant="0xflag")
    file_appeal(c, case_id, appellant="0xpub")


def test_either_party_can_appeal_partial():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="PARTIAL")
    file_appeal(c, case_id, appellant="0xflag")


def test_uninvolved_party_cannot_appeal_partial():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="PARTIAL")
    with assert_raises():
        file_appeal(c, case_id, appellant="0xsomeoneelse")


def test_wrong_stake_rejected():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    set_time(10)
    gl.message.sender_address = Address("0xpub")
    gl.message.value = u256(1)
    with assert_raises():
        c.file_appeal(case_id)
    gl.message.value = u256(0)


def test_appeal_after_window_rejected():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    with assert_raises():
        file_appeal(c, case_id, appellant="0xpub", now=APPEAL_WINDOW_SECONDS + 100)


def test_expire_if_unappealed():
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    set_time(APPEAL_WINDOW_SECONDS + 100)
    c.expire_if_unappealed(case_id)
    assert c.get_case(case_id)["status"] == "FINALIZED_BY_DEFAULT"
    assert c.get_case(case_id)["final_verdict"] == "VIOLATION"
