from stub_genlayer import gl, Address, u256, assert_raises
from helpers import new_contract, register_jurors, submit_and_verdict, file_appeal, run_round
from modappeal import JUROR_REGISTRATION_STAKE, APPEAL_WINDOW_SECONDS


def test_submit_flag_requires_platform_to_be_the_sender():
    """A case cannot be opened on behalf of a platform address that didn't
    actually send the transaction -- this is the "platform submissions are
    authenticated" requirement."""
    c = new_contract()
    gl.message.sender_address = Address("0xattacker")
    with assert_raises():
        c.submit_flag("0xplat", "0xflag", "0xpub", "content-1", ["https://example.com/a"])


def test_submit_flag_succeeds_when_sender_is_the_platform():
    c = new_contract()
    gl.message.sender_address = Address("0xplat")
    case_id = c.submit_flag("0xplat", "0xflag", "0xpub", "content-1", ["https://example.com/a"])
    assert int(case_id) == 1


def test_register_as_juror_requires_exact_registration_stake():
    c = new_contract()
    gl.message.sender_address = Address("0xj0")
    gl.message.value = u256(int(JUROR_REGISTRATION_STAKE) - 1)
    with assert_raises():
        c.register_as_juror()
    gl.message.value = u256(0)


def test_register_as_juror_rejects_duplicate_registration():
    c = new_contract()
    gl.message.sender_address = Address("0xj0")
    gl.message.value = JUROR_REGISTRATION_STAKE
    c.register_as_juror()
    with assert_raises():
        c.register_as_juror()  # same address, still paying correctly -> still rejected
    gl.message.value = u256(0)


def test_register_as_juror_stake_is_banked_in_treasury():
    c = new_contract()
    gl.message.sender_address = Address("0xj0")
    gl.message.value = JUROR_REGISTRATION_STAKE
    c.register_as_juror()
    gl.message.value = u256(0)
    assert int(c.treasury) == int(JUROR_REGISTRATION_STAKE)


def test_get_case_for_jury_exposes_full_case_context():
    """A selected juror must be able to see the content, evidence, the
    bound policy, and the parties involved -- not just a bare verdict."""
    c = new_contract()
    case_id = submit_and_verdict(c, verdict="VIOLATION", content_id="content-xyz",
                                  evidence=["https://example.com/xyz"])
    info = c.get_case_for_jury(case_id)
    assert info["content_id"] == "content-xyz"
    assert list(info["evidence_urls"]) == ["https://example.com/xyz"]
    assert "Content Moderation Policy" in info["policy"]
    assert info["platform"] == "0xplat"
    assert info["flagger"] == "0xflag"
    assert info["publisher"] == "0xpub"
    assert info["automated_verdict"] == "VIOLATION"
    # bound to a specific fetched-content snapshot, not just a URL
    assert info["evidence_content_hash"] != ""


def test_evidence_content_hash_is_bound_at_auto_verdict_and_stable():
    """The case is bound to a specific hash of the fetched evidence content
    at automated-verdict time -- immutable evidence content binding, not
    just the URL list."""
    c = new_contract()
    case_id = submit_and_verdict(c, verdict="NO_VIOLATION")
    case = c.get_case(case_id)
    first_hash = case["evidence_content_hash"]
    assert first_hash != ""
    # calling get_case again returns the identical, frozen hash
    assert c.get_case(case_id)["evidence_content_hash"] == first_hash


def test_complete_workflow_with_all_protections_then_unappealed_finalization():
    """End-to-end: authenticated submission, staked juror registration,
    content-hash-bound automated verdict, full jury-visible case context,
    then the unappealed-finalization path (no appeal filed in time)."""
    c = new_contract()
    register_jurors(c, [f"0xj{i}" for i in range(5)])

    gl.message.sender_address = Address("0xplat")
    case_id = c.submit_flag("0xplat", "0xflag", "0xpub", "post-1", ["https://example.com/a"])
    gl.nondet.exec_prompt = lambda prompt: "NO_VIOLATION"
    c.auto_verdict(case_id)

    info = c.get_case_for_jury(case_id)
    assert info["automated_verdict"] == "NO_VIOLATION"
    assert info["evidence_content_hash"] != ""

    from stub_genlayer import set_time
    set_time(APPEAL_WINDOW_SECONDS + 100)  # let the appeal window pass, unappealed
    c.expire_if_unappealed(case_id)

    final = c.get_case(case_id)
    assert final["status"] == "FINALIZED_BY_DEFAULT"
    assert final["final_verdict"] == "NO_VIOLATION"
