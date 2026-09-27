# ModAppeal — Architecture

## 1. Purpose

ModAppeal is a GenLayer Intelligent Contract that adjudicates content-moderation
disputes. A platform flags content; GenLayer issues an automated verdict
(`VIOLATION` / `NO_VIOLATION` / `PARTIAL`); either the publisher or the flagger
may appeal; a staked jury reviews frozen evidence via commit-reveal; and — the
core feature — a jury round that fails to reach majority automatically
escalates to a larger, entirely new jury, up to a hard cap, rather than
silently reverting to the automated verdict.

## 2. Actors

| Actor | Role |
|---|---|
| **Platform** | Registers content flags; must call `submit_flag` itself (authenticated — see §7) |
| **Publisher** | Owner of the flagged content; may appeal a VIOLATION verdict; may appeal a PARTIAL verdict jointly with the flagger |
| **Flagger** | Party who raised the flag; may appeal a NO_VIOLATION verdict; may appeal a PARTIAL verdict jointly with the publisher |
| **Juror** | Stakes GEN to vote in commit-reveal rounds |
| **Contract (self)** | Selects juries, freezes evidence, escalates, finalizes |

## 3. State Machine

```
AUTOMATED_VERDICT
   │
   ├─ no appeal within APPEAL_WINDOW ──────────────► FINALIZED_BY_DEFAULT
   │
   ▼
APPEALED (appeal stake forfeited → reward pool, evidence frozen)
   │
   ▼
JURY_COMMIT(round 1, 5 jurors) → JURY_REVEAL(round 1) → tally
   │
   ├─ majority reached ─────────────────────────────► FINALIZED
   │
   └─ no majority
        │ escalation_count += 1
        ├─ escalation_count ≤ MAX_ESCALATIONS (2):
        │     new round, 9 NEW jurors, SAME frozen evidence
        │     ──► JURY_COMMIT(round N) → ... → tally
        │
        └─ escalation_count > MAX_ESCALATIONS ──────► FINALIZED_BY_DEADLOCK
```

Up to 3 total jury rounds (1 base + 2 escalations), guaranteeing termination.
Terminal states: `FINALIZED`, `FINALIZED_BY_DEFAULT`, `FINALIZED_BY_DEADLOCK`.

## 4. Majority rule (exact invariant)

```
A candidate reaches majority iff:  candidate_votes * 2 > revealed_votes
```

Non-reveals are excluded from `revealed_votes` entirely. If no candidate
(`VIOLATION` / `NO_VIOLATION` / `PARTIAL`) satisfies this, the round escalates.

## 5. Economics

| Parameter | v1 value |
|---|---|
| `APPEAL_STAKE` | 10 GEN |
| `JUROR_STAKE` | 2 GEN |
| `JUROR_REGISTRATION_STAKE` | 1 GEN (non-refundable, banked to treasury — see §8) |
| `BASE_JURY_SIZE` | 5 |
| `ESCALATION_JURY_SIZE` | 9 |
| `MAX_ESCALATIONS` | 2 (up to 2 escalations beyond the base round → 3 jury rounds max) |

**Principal vs. reward pool, kept strictly separate:**
- `JUROR_STAKE` is each juror's own principal for that round. Returned in full
  unless that juror committed and then failed to reveal (griefing) — that
  stake is slashed and added to the reward pool, regardless of the round's
  outcome or escalation status. A juror who never committed at all has no
  stake in the contract and is never slashed.
- `APPEAL_STAKE` is forfeited into the reward pool unconditionally at filing
  time — win or lose, there is no "loser's stake" to seize later, which keeps
  the economics independent of who turns out to be right.

**Distribution, on the round that reaches `FINALIZED`:**
```
correct_reveals = revealed jurors in the deciding round who voted for the winning side
reward_per_correct_juror = reward_pool / correct_reveals   (integer division)
remainder → treasury (never a juror)
```
- Correct jurors: principal returned + `reward_per_correct_juror`.
- Minority jurors (revealed, wrong side): principal returned only. Voting
  against the majority is not treated as misconduct — only a failure to
  reveal is penalized.
- Non-final rounds (escalated away without reaching majority): every juror
  who revealed gets their principal back only; no reward is distributed
  until a round actually decides the case.

**`FINALIZED_BY_DEADLOCK`** (cap hit, no round ever reached majority): the
accumulated reward pool is split evenly across every juror, from every round,
who revealed at least once (`deadlock_eligible` is cumulative across rounds
by design — a juror who did their job in round 1 is still owed a share even
if the case ultimately deadlocks in round 3). Division uses integer
arithmetic (`pool // n`); the remainder is routed to the contract's
`treasury` balance, not to any individual juror, so the split is deterministic
regardless of jury size.

**Claiming:** every credit (principal + reward) accumulates in a single
`claimable_rewards[address]` balance, aggregated across cases and rounds.
`claim()` is a one-time pull payment — it zeroes the balance before paying
out. If the payout transfer itself fails, `__on_errored_message__` restores
the balance so the funds are never silently lost.

## 6. Evidence freeze

At `file_appeal`, the case's evidence URLs are hashed (`sha256` over the
sorted URL list) and stored as `evidence_hash`. No public method can modify
`evidence_urls` after this point, so the hash is stable for the lifetime of
the appeal across every jury round, including escalations.

## 7. Automated verdict mechanism

`auto_verdict()` does not treat the leader's LLM call as authoritative once
it merely looks well-formed. Each validator performs **meaningful,
independent adjudication**:

1. The leader fetches the content of every `evidence_url` on the case via
   `gl.nondet.web.render` (not just the bare URL string), and asks the model
   to apply a fixed, written policy (`MODERATION_POLICY`, in-contract) to
   that fetched content, returning one of `VIOLATION` / `NO_VIOLATION` /
   `PARTIAL`.
2. Every validator **independently repeats the same fetch-and-judge
   process** — its own `gl.nondet.web.render` calls, the same bound policy —
   and requires its own result to **exactly match** the leader's claimed
   verdict. A validator whose independent re-derivation disagrees rejects
   the leader's proposal (`validator_fn` returns `False`), which GenVM
   treats as a genuine disagreement, not a rubber-stamp of the label's
   format.
3. This mirrors the "leader-plus-validator re-derivation" pattern already
   used successfully elsewhere in this project's history (Cascade's fix
   requiring every validator to independently fetch evidence and score it,
   rather than only audit the leader's self-reported JSON).

The bound policy is intentionally narrow (§10 discusses categories not
covered) so that "apply the policy" is a concrete, checkable instruction
rather than an open-ended judgment call — this is what makes independent
re-derivation converge instead of just producing noise.

**Fail-closed on unfetchable evidence:** if every cited `evidence_url` fails
to fetch, `auto_verdict()` raises rather than issuing a verdict against a
placeholder "unable to fetch" string — there is no such thing as a verdict
grounded in no evidence. A partial fetch (some URLs succeed, some don't) is
still tolerated, since partial evidence is still real evidence.

**Evidence content is bound at the same consensus round as the verdict.**
The leader and every validator don't just agree on a verdict string — they
agree on `"VERDICT|content_hash"`, where `content_hash` is
`sha256(fetched_content)`. This binds the case to a specific, consensus-
agreed hash of the *fetched bytes*, not just the URL list (`evidence_hash`,
§6, is still only a hash of the URLs). The agreed hash is stored as
`Case.evidence_content_hash` and is exposed to jurors via
`get_case_for_jury()` (§9), so a case is bound to authenticated evidence
content from the moment of the automated verdict onward, not just from the
moment of appeal.

**Platform submissions are authenticated.** `submit_flag(platform, ...)`
requires `gl.message.sender_address == Address(platform)` — only the
platform address itself can open a case on its own behalf. Nobody can
submit a flag that impersonates a platform they don't control.

## 8. Juror selection

```python
def sort_key(addr):
    return hashlib.sha256(f"{case_id}:{addr}".encode()).hexdigest()
return sorted(available, key=sort_key)[:size]
```

Selection is **deterministic and case-specific**, not verifiable randomness.
Every case gets a different ordering of the registered pool, so no single
early-registered address is always picked — but it is not cryptographically
random, and a determined address knowing the algorithm could in principle
predict its own odds for a specific case_id. This is a stated v1 trade-off,
not a claim of fairness guarantees; a future version could use a
GenLayer-native randomness/VRF primitive instead.

**Registration now costs a real stake.** `register_as_juror()` requires
paying `JUROR_REGISTRATION_STAKE` (1 GEN), non-refundable, banked into
`treasury`. This directly targets "final juries captured through free
predictable multi-address registration": since selection is deterministic
and an attacker who knows the algorithm could in principle compute which of
their own addresses would win for a specific `case_id`, the previous free
registration meant that capturing a jury only cost the gas to register
arbitrarily many candidate addresses. A real per-address stake makes this
proportional to GEN spent, not free. This is a mitigation, not a complete
solve — a well-funded attacker can still register many addresses — but it
removes the "free" half of the attack, which is the concrete gap the fix
addresses. Combined with case-specific (not globally reusable) selection,
the cost of biasing any one case's jury now scales with the number of
distinct addresses an attacker is willing to fund.

## 9. Jury-visible case context

`get_case_for_jury(case_id)` gives a selected juror everything needed to
cast an informed vote in one call, rather than expecting them to
reconstruct context from separate calls or trust an off-chain description:
`content_id`, `evidence_urls`, `evidence_hash`, `evidence_content_hash`,
the full `MODERATION_POLICY` text, and the `platform` / `flagger` /
`publisher` addresses involved. This is distinct from `get_round()` (the
jury composition and vote tallies for one round) and `get_case()`
(status/verdict summary) — `get_case_for_jury` is specifically the
case-content view a juror needs before committing a vote.

## 10. Honest v1 limitations

- **Juror registration cost is a mitigation, not a full solve.** §8 covers
  this in detail: registration now costs `JUROR_REGISTRATION_STAKE`, which
  removes the "free" half of sybil-capturing a jury, but a well-funded
  attacker can still register many addresses — it raises the cost, it
  doesn't make capture impossible. Deferred to v2: reputation-weighted
  eligibility, or a GenLayer-native randomness primitive for selection
  itself (§8) so knowing the algorithm no longer helps at all.
- **`auto_verdict()` is intentionally permissionless.** Anyone can trigger it
  once a case exists; the meaningful adjudication (§7) happens inside the
  consensus mechanism itself regardless of who calls the function, so
  restricting the caller was judged unnecessary for v1.
- **Evidence content is hashed once, at automated-verdict time, not
  re-verified live at jury time.** `evidence_content_hash` (§7) binds the
  case to the fetched content the automated verdict was based on, and is
  exposed to jurors via `get_case_for_jury()` (§9). But a jury round does
  not itself re-fetch and re-check evidence against that hash — a juror is
  trusted to review the cited evidence and vote accordingly, the same way a
  human juror would. A stronger v2 could have the jury's own commit-reveal
  vote include an attestation that their review matched the recorded hash.
- **The bound moderation policy is narrow and fixed.** `MODERATION_POLICY`
  (§7) only names a few concrete categories (violence threats, hate speech
  targeting protected characteristics, CSAM) — real moderation policy is
  broader. This is a deliberate v1 scope choice: a narrow, concrete policy is
  what makes independent multi-validator re-derivation converge instead of
  producing noise on a vague standard.
- **No cross-case reputation registry.** Every juror stakes fresh per case;
  there is no track record that affects future eligibility or reward share.
- **Single-file contract.** No separate escrow or registry contract — there
  was no natural boundary at this scope to justify the split.

## 11. Corrections discovered only through live testing

The offline test suite (32 tests, custom runner) proves the contract's
business logic, but a stub cannot catch GenVM-runtime-specific API
mismatches. These were only found by deploying to GenLayer Studio and are
recorded here since they are not documented anywhere else at the time of
writing:

- The message sender is `gl.message.sender_address`, not `gl.message.sender`.
- There is no `gl.message.timestamp`; current time is read via plain
  `datetime.datetime.now()`, which GenVM makes deterministic across
  validators.
- Persistent fields (`TreeMap`, `DynArray`) must be declared as class-level
  type annotations on the `gl.Contract` subclass — assigning them only inside
  `__init__` means they silently fail to persist between transactions.
- Custom classes stored inside a `TreeMap` (like `Case` or `Round`) must be
  decorated `@allow_storage @dataclass`, with internal collections typed as
  `TreeMap`/`DynArray` rather than plain `dict`/`list`/`set`.
- `gl.storage.inmem_allocate(DynArray[...])` fails at runtime in this GenVM
  version even though it is the documented pattern; assign a plain Python
  list directly to the `DynArray`-typed field instead — the storage layer
  converts it on assignment.
- A `datetime.datetime.min` sentinel placeholder crashes storage encoding
  (`ValueError: year 0 is out of range`); use a real current-time value as
  any "not yet set" placeholder instead.
- `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` takes positional-only
  arguments; calling it with keyword arguments raises a `TypeError`.
- Passing a lambda that calls a bound instance method (`self._foo(...)`)
  into `gl.vm.run_nondet_unsafe` captures `self` — the whole contract,
  storage fields included — in the closure, which GenVM cannot pickle for
  the sandboxed nondet worker. The leader/validator functions must be
  module-level (or otherwise free of any `self` reference) so only plain,
  in-memory values cross into the nondet block.
- The wrapped leader result passed to `validator_fn` is a `gl.vm.Return`
  instance; the leader's actual return value is on `.calldata`, not `.value`.
  An error raised inside `validator_fn` is treated as an automatic
  "disagree" rather than crashing the transaction, so a wrong attribute name
  here fails silently as a stuck `Undetermined` consensus result rather than
  a visible exception.
- `emit_transfer()` sends value to a plain wallet (EOA) with only
  `value=...` — passing an `on=...` trigger name (meant for contract
  recipients) causes a low-level `SystemError: 2: inval` on the transfer.

## 12. Deployed instances (GenLayer Studio)

- **Production contract** (real windows: 3-day appeal, 24h commit, 24h
  reveal), redeployed after the §7 validator-adjudication fix and the §8/§9
  steward-requested protections (registration stake, platform
  authentication, evidence-content binding, jury-visible case context):
  *pending redeploy — update this address once the corrected contract is
  live.*
- The full happy-path flow (submit → automated verdict → appeal → 5-juror
  commit-reveal → majority tally → claim) was live-tested end-to-end on
  Studio with short-window testnet variants before the §7 fix's production
  deploy. Escalation (5→9 jurors) and deadlock (3 failed rounds) are covered
  by the offline test suite, including exact GEN-accounting invariants
  across multiple rounds, but were not additionally repeated live given the
  offline coverage already exercises those paths precisely.
