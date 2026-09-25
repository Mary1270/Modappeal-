from stub_genlayer import gl, Address, assert_raises
from helpers import new_contract, register_jurors


def test_validator_rejects_leader_when_independent_rederivation_disagrees():
    """If a validator's own independent re-derivation of the verdict differs
    from the leader's claimed verdict, the validator must reject it -- this
    is what makes the check "meaningful adjudication" rather than a bare
    label/format check."""
    c = new_contract()
    gl.message.sender_address = Address("0xplat")
    case_id = c.submit_flag("0xplat", "0xflag", "0xpub", "content-1", ["https://example.com/a"])

    call_count = {"n": 0}

    def flaky_exec_prompt(prompt):
        call_count["n"] += 1
        # first call (the "leader") says VIOLATION; every call after
        # (independent validator re-derivations) says something else --
        # simulating a validator whose own re-derivation disagrees.
        return "VIOLATION" if call_count["n"] == 1 else "NO_VIOLATION"

    gl.nondet.exec_prompt = flaky_exec_prompt
    with assert_raises():
        c.auto_verdict(case_id)


def test_auto_verdict_grounds_prompt_in_fetched_evidence_content():
    """The prompt sent to the model must include the actually-fetched
    evidence content (via gl.nondet.web.render), not just the bare URL --
    this is the "fetch the cited evidence" requirement."""
    c = new_contract()
    gl.message.sender_address = Address("0xplat")
    case_id = c.submit_flag(
        "0xplat", "0xflag", "0xpub", "content-1", ["https://example.com/a"]
    )

    seen_prompts = []

    def capturing_exec_prompt(prompt):
        seen_prompts.append(prompt)
        return "NO_VIOLATION"

    gl.nondet.exec_prompt = capturing_exec_prompt
    c.auto_verdict(case_id)

    assert len(seen_prompts) >= 1
    for prompt in seen_prompts:
        assert "[stub content for https://example.com/a]" in prompt
        assert "Content Moderation Policy" in prompt


def test_auto_verdict_refuses_when_all_evidence_unfetchable():
    """If every evidence URL fails to fetch, no verdict should be issued
    against a placeholder string -- the call must fail instead."""
    c = new_contract()
    gl.message.sender_address = Address("0xplat")
    case_id = c.submit_flag(
        "0xplat", "0xflag", "0xpub", "content-1", ["https://unreachable.example/a"]
    )

    def failing_render(url):
        raise Exception("connection refused")

    original_render = gl.nondet.web.render
    gl.nondet.web.render = failing_render
    try:
        with assert_raises():
            c.auto_verdict(case_id)
    finally:
        gl.nondet.web.render = original_render
