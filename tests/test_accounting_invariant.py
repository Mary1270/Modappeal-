from stub_genlayer import gl, Address, u256
from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round
from test_escalation import split_no_majority
from modappeal import APPEAL_STAKE, JUROR_STAKE


def total_contract_balance(appeal_stake: int, rounds_juror_counts: list) -> int:
    """Every GEN that was ever sent into the contract: the appeal stake plus
    one JUROR_STAKE per commit_vote call, across every round that ran."""
    return appeal_stake + sum(n * int(JUROR_STAKE) for n in rounds_juror_counts)


def sum_all_claimable(c, addrs) -> int:
    return sum(int(c.get_claimable(a)) for a in addrs)


def test_multiround_escalation_accounting_balances():
    """PARTIAL verdict -> either party appeals -> round 1 (5 jurors, 3-way
    split, no majority) -> round 2 (9 NEW jurors, majority reached).
    Every GEN that entered the contract must end up either claimable by
    someone or in the treasury -- nothing lost, nothing invented."""
    c = new_contract()
    pool_addrs = [f"0xj{i}" for i in range(40)]
    register_jurors(c, pool_addrs)

    case_id = submit_and_verdict(c, verdict="PARTIAL")
    file_appeal(c, case_id, appellant="0xflag")  # PARTIAL: either party may appeal

    round1_jurors = c.get_round(case_id, 1)["jurors"]
    votes1 = split_no_majority(round1_jurors)  # 3-way split -> no majority
    t = run_round(c, case_id, 1, votes1, start_time=2000)

    case_mid = c.get_case(case_id)
    assert case_mid["current_round_id"] == 2
    assert case_mid["escalation_count"] == 1

    round2_jurors = c.get_round(case_id, 2)["jurors"]
    assert len(round2_jurors) == 9
    assert set(round2_jurors).isdisjoint(set(round1_jurors))

    # 5 correct (majority) / 4 minority -- all reveal
    votes2 = {addr: "VIOLATION" for addr in round2_jurors[:5]}
    votes2.update({addr: "NO_VIOLATION" for addr in round2_jurors[5:]})
    run_round(c, case_id, 2, votes2, start_time=t + 1000)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED"
    assert final["final_verdict"] == "VIOLATION"
    assert final["reward_pool"] == 0

    all_jurors_ever_staked = round1_jurors + round2_jurors
    total_in = total_contract_balance(int(APPEAL_STAKE), [len(round1_jurors), len(round2_jurors)])
    total_out = sum_all_claimable(c, all_jurors_ever_staked) + int(c.treasury)
    assert total_out == total_in

    # everyone who revealed (in any round) got at least their own stake back
    for addr in all_jurors_ever_staked:
        assert int(c.get_claimable(addr)) >= int(JUROR_STAKE)


def test_three_round_deadlock_accounting_balances():
    """Round 1 (5) -> round 2 (9) -> round 3 (9), all three fail to reach
    majority -> FINALIZED_BY_DEADLOCK. Every GEN staked across all three
    rounds plus the appeal stake must be fully accounted for."""
    c = new_contract()
    pool_addrs = [f"0xj{i}" for i in range(40)]
    register_jurors(c, pool_addrs)

    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")

    t = 2000
    round_sizes = []
    all_jurors = []
    for round_id in (1, 2, 3):
        jurors = c.get_round(case_id, round_id)["jurors"]
        round_sizes.append(len(jurors))
        all_jurors.extend(jurors)
        votes = split_no_majority(jurors)
        t = run_round(c, case_id, round_id, votes, start_time=t + 1000)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED_BY_DEADLOCK"

    total_in = total_contract_balance(int(APPEAL_STAKE), round_sizes)
    total_out = sum_all_claimable(c, all_jurors) + int(c.treasury)
    assert total_out == total_in
