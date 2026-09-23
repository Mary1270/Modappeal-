from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round


def split_no_majority(jurors):
    """Round-robin votes across the 3 verdicts so no candidate can reach a
    strict majority (max count is ceil(n/3), which never exceeds n/2 for
    the jury sizes used here: 5 -> 2/2/1, 9 -> 3/3/3)."""
    options = ["VIOLATION", "NO_VIOLATION", "PARTIAL"]
    return {addr: options[i % 3] for i, addr in enumerate(jurors)}


def test_round1_fails_round2_escalates_with_new_jurors_and_succeeds():
    c = new_contract()
    pool = [f"0xj{i}" for i in range(40)]
    register_jurors(c, pool)

    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")

    round1_jurors = c.get_round(case_id, 1)["jurors"]
    assert len(round1_jurors) == 5
    votes1 = split_no_majority(round1_jurors)
    t = run_round(c, case_id, 1, votes1, start_time=2000)

    case_state = c.get_case(case_id)
    assert case_state["status"] == "JURY_COMMIT"
    assert case_state["escalation_count"] == 1
    assert case_state["current_round_id"] == 2

    round2_jurors = c.get_round(case_id, 2)["jurors"]
    assert len(round2_jurors) == 9
    assert set(round2_jurors).isdisjoint(set(round1_jurors))

    votes2 = {addr: "VIOLATION" for addr in round2_jurors}
    run_round(c, case_id, 2, votes2, start_time=t + 1000)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED"
    assert final["final_verdict"] == "VIOLATION"
    assert final["escalation_count"] == 1


def test_three_failed_rounds_deadlock():
    c = new_contract()
    pool = [f"0xj{i}" for i in range(40)]
    register_jurors(c, pool)

    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")

    t = 2000
    for round_id in (1, 2, 3):
        jurors = c.get_round(case_id, round_id)["jurors"]
        votes = split_no_majority(jurors)
        t = run_round(c, case_id, round_id, votes, start_time=t + 1000) 

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED_BY_DEADLOCK"
    assert final["escalation_count"] == 3
