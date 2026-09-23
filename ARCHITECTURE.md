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
| **Platform** | Registers content flags |
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

## 7. Juror selection

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

## 8. Honest v1 limitations

- **No juror-registration stake.** `register_as_juror()` is free, so a sybil
  actor could register many addresses cheaply. Combined with the
  case-specific (not random) selection above, this is a real, disclosed
  weakness for a production deployment — deferred to v2 (e.g. a registration
  bond, or reputation-weighted eligibility).
- **`auto_verdict()` is intentionally permissionless.** Anyone can trigger it
  once a case exists; it only evaluates evidence already on record and carries
  no economic risk, so restricting the caller was judged unnecessary for v1.
- **Evidence hash covers the URL list, not fetched page content.** The
  automated verdict and the jury both reason from URLs and metadata supplied
  at flag time; the contract does not fetch and hash the underlying page
  content itself. A URL's live content could differ from what was reviewed.
  A stronger v2 would use `gl.nondet.web` to fetch and hash content directly.
- **No cross-case reputation registry.** Every juror stakes fresh per case;
  there is no track record that affects future eligibility or reward share.
- **Single-file contract.** No separate escrow or registry contract — there
  was no natural boundary at this scope to justify the split.

## 9. Corrections discovered only through live testing

The offline test suite (21 tests, custom runner) proves the contract's
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

## 10. Deployed instances (GenLayer Studio)

- **Production contract** (real windows: 3-day appeal, 24h commit, 24h
  reveal): `0x0A9366c82a782c6f1FCa3263C4e318e9Ce158913`
- The full happy-path flow (submit → automated verdict → appeal → 5-juror
  commit-reveal → majority tally → claim) was live-tested end-to-end on
  Studio with short-window testnet variants before this production deploy.
  Escalation (5→9 jurors) and deadlock (3 failed rounds) are covered by the
  offline test suite, including exact GEN-accounting invariants across
  multiple rounds, but were not additionally repeated live given the offline
  coverage already exercises those paths precisely.
