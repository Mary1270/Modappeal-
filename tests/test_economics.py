from stub_genlayer import gl, Address, u256, commitment_hash, assert_raises, set_time
from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round
from modappeal import APPEAL_STAKE, JUROR_STAKE, COMMIT_WINDOW_SECONDS, REVEAL_WINDOW_SECONDS


def test_correct_jurors_get_stake_plus_reward_minority_gets_stake_only():
    c = new_contract()
    jurors = [f"0xj{i}" for i in range(5)]
    register_jurors(c, jurors)

    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")

    round_jurors = c.get_round(case_id, 1)["jurors"]
    votes = {addr: "VIOLATION" for addr in round_jurors[:3]}
    votes.update({addr: "NO_VIOLATION" for addr in round_jurors[3:]})
    run_round(c, case_id, 1, votes, start_time=2000)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED"
    assert final["final_verdict"] == "VIOLATION"

    pool = int(APPEAL_STAKE)
    n_correct = 3
    reward_each = pool // n_correct
    remainder = pool - reward_each * n_correct

    for addr in round_jurors[:3]:
        assert int(c.get_claimable(addr)) == int(JUROR_STAKE) + reward_each
    for addr in round_jurors[3:]:
        assert int(c.get_claimable(addr)) == int(JUROR_STAKE)
    assert int(c.treasury) == remainder
    assert final["reward_pool"] == 0


def test_non_reveal_is_slashed_and_feeds_reward_pool():
    c = new_contract()
    jurors = [f"0xj{i}" for i in range(5)]
    register_jurors(c, jurors)

    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")
    round_jurors = c.get_round(case_id, 1)["jurors"]

    votes = {addr: "VIOLATION" for addr in round_jurors}
    non_revealer = round_jurors[0]

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
        if addr == non_revealer:
            continue
        gl.message.sender_address = Address(addr)
        c.reveal_vote(case_id, 1, verdict, salts[addr])
    set_time(commit_time + COMMIT_WINDOW_SECONDS + REVEAL_WINDOW_SECONDS + 2)
    c.tally_round(case_id, 1)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED"

    # non-revealer forfeits their stake entirely -- no claimable balance at all
    assert int(c.get_claimable(non_revealer)) == 0

    revealers = [a for a in round_jurors if a != non_revealer]
    pool = int(APPEAL_STAKE) + int(JUROR_STAKE)  # appeal stake + slashed non-reveal stake
    n_correct = len(revealers)  # all remaining revealers voted VIOLATION, the winner
    reward_each = pool // n_correct
    for addr in revealers:
        assert int(c.get_claimable(addr)) == int(JUROR_STAKE) + reward_each


def test_non_committer_gets_no_stake_no_slash():
    c = new_contract()
    jurors = [f"0xj{i}" for i in range(5)]
    register_jurors(c, jurors)
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")
    round_jurors = c.get_round(case_id, 1)["jurors"]
    non_committer = round_jurors[0]
    committers = round_jurors[1:]

    votes = {addr: "VIOLATION" for addr in committers}
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

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED"
    # non-committer never staked, so nothing to slash and nothing claimable
    assert int(c.get_claimable(non_committer)) == 0
    # pool only ever held the appeal stake -- no non-reveal slash occurred
    pool = int(APPEAL_STAKE)
    n_correct = len(committers)
    reward_each = pool // n_correct
    for addr in committers:
        assert int(c.get_claimable(addr)) == int(JUROR_STAKE) + reward_each


def test_on_errored_message_restores_claimable_balance():
    """If a claim payout bounces back (recipient rejects the transfer),
    __on_errored_message__ must credit the balance back rather than lose it."""
    c = new_contract()
    gl.message.sender_address = Address("0xjuror")
    gl.message.value = u256(500)
    c.__on_errored_message__()
    gl.message.value = u256(0)
    assert int(c.get_claimable("0xjuror")) == 500


def test_claim_is_one_time_and_zeroes_balance():
    c = new_contract()
    jurors = [f"0xj{i}" for i in range(5)]
    register_jurors(c, jurors)
    case_id = submit_and_verdict(c, verdict="VIOLATION")
    file_appeal(c, case_id, appellant="0xpub")
    round_jurors = c.get_round(case_id, 1)["jurors"]
    votes = {addr: "VIOLATION" for addr in round_jurors}
    run_round(c, case_id, 1, votes, start_time=2000)

    juror = round_jurors[0]
    assert int(c.get_claimable(juror)) > 0
    gl.message.sender_address = Address(juror)
    c.claim()
    assert int(c.get_claimable(juror)) == 0
    with assert_raises():
        c.claim()  # nothing left to claim
