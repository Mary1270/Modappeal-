import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contracts"))

from stub_genlayer import gl, Address, u256, commitment_hash, reset, set_time, install_fake_clock  # noqa: E402
import modappeal  # noqa: E402
from modappeal import ModAppeal, APPEAL_STAKE, JUROR_STAKE, COMMIT_WINDOW_SECONDS, REVEAL_WINDOW_SECONDS  # noqa: E402

install_fake_clock(modappeal)


def new_contract():
    reset()
    return ModAppeal()


def register_jurors(c, addrs):
    for a in addrs:
        gl.message.sender_address = Address(a)
        c.register_as_juror()


def submit_and_verdict(c, verdict="NO_VIOLATION", platform="0xplat", flagger="0xflag",
                        publisher="0xpub", content_id="content-1", evidence=None):
    evidence = evidence or ["https://example.com/a"]
    gl.message.sender_address = Address(platform)
    case_id = c.submit_flag(platform, flagger, publisher, content_id, evidence)
    modappeal.gl.nondet.exec_prompt = lambda prompt: verdict
    c.auto_verdict(case_id)
    return case_id


def file_appeal(c, case_id, appellant, now=1000):
    set_time(now)
    gl.message.sender_address = Address(appellant)
    gl.message.value = APPEAL_STAKE
    c.file_appeal(case_id)
    gl.message.value = u256(0)


def run_round(c, case_id, round_id, votes: dict, start_time, non_revealers=()):
    """votes: {address: verdict}. non_revealers: subset of votes' addresses that commit but don't reveal."""
    commit_time = start_time
    set_time(commit_time)
    salts = {addr: f"salt-{addr}" for addr in votes}
    for addr, verdict in votes.items():
        gl.message.sender_address = Address(addr)
        gl.message.value = JUROR_STAKE
        c.commit_vote(case_id, round_id, commitment_hash(verdict, salts[addr]))
    gl.message.value = u256(0)

    close_time = commit_time + COMMIT_WINDOW_SECONDS + 1
    set_time(close_time)
    c.close_commit(case_id, round_id)

    for addr, verdict in votes.items():
        if addr in non_revealers:
            continue
        gl.message.sender_address = Address(addr)
        c.reveal_vote(case_id, round_id, verdict, salts[addr])

    tally_time = close_time + REVEAL_WINDOW_SECONDS + 1
    set_time(tally_time)
    c.tally_round(case_id, round_id)
    return tally_time
