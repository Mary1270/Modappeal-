from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round
from test_escalation import split_no_majority


def test_evidence_hash_frozen_at_appeal_and_stable_across_rounds():
    c = new_contract()
    jurors = [f"0xj{i}" for i in range(30)]
    register_jurors(c, jurors)

    case_id = submit_and_verdict(c, verdict="VIOLATION", evidence=["https://a.com/x", "https://b.com/y"])
    file_appeal(c, case_id, appellant="0xpub")

    case_before = c.get_case(case_id)
    hash_before = case_before["evidence_hash"]
    assert hash_before is not None

    round1_jurors = c.get_round(case_id, 1)["jurors"]
    votes = split_no_majority(round1_jurors)  # force no majority -> escalation
    t = run_round(c, case_id, 1, votes, start_time=2000)

    case_after = c.get_case(case_id)
    assert case_after["evidence_hash"] == hash_before
    assert case_after["current_round_id"] == 2
