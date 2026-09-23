from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round
from test_escalation import split_no_majority
from modappeal import APPEAL_STAKE


def test_deadlock_splits_pool_and_sends_remainder_to_treasury():
    c = new_contract()
    pool = [f"0xj{i}" for i in range(40)]
    register_jurors(c, pool)

    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")

    t = 2000
    all_revealers = []
    for round_id in (1, 2, 3):
        jurors = c.get_round(case_id, round_id)["jurors"]
        all_revealers.extend(jurors)
        votes = split_no_majority(jurors)
        t = run_round(c, case_id, round_id, votes, start_time=t + 1000)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED_BY_DEADLOCK"
    assert final["reward_pool"] == 0  # fully distributed (minus remainder to treasury)

    # pool = appeal stake only, since every juror both committed AND revealed
    # (no non-reveal slashing contributed extra funds in this scenario)
    pool_amount = int(APPEAL_STAKE)
    n = len(all_revealers)
    expected_each = pool_amount // n
    expected_remainder = pool_amount - expected_each * n

    total_claimable_consolation = sum(
        int(c.get_claimable(addr)) for addr in all_revealers
    ) - sum_of_returned_stakes(c, all_revealers)

    # every juror's own stake is returned in full regardless of round outcome
    assert total_claimable_consolation == expected_each * n
    assert c.treasury == expected_remainder


def sum_of_returned_stakes(contract, addrs):
    from modappeal import JUROR_STAKE
    return int(JUROR_STAKE) * len(addrs)
