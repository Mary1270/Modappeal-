# ModAppeal

A GenLayer Intelligent Contract for content-moderation appeals: an automated
AI verdict on flagged content, a staked commit-reveal jury for appeals, and
automatic escalation to a larger jury when the first jury can't reach a
majority — instead of silently falling back to the original verdict.

Live on GenLayer Studio: `0x0A9366c82a782c6f1FCa3263C4e318e9Ce158913`

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full state machine, economics,
and a list of GenVM runtime quirks discovered while building this (several are
undocumented anywhere else).

## How it works

1. A platform flags content — `submit_flag(platform, flagger, publisher, content_id, evidence_urls)`.
2. Anyone can trigger the automated verdict — `auto_verdict(case_id)`. GenLayer's
   validators reach consensus on one of `VIOLATION` / `NO_VIOLATION` / `PARTIAL`.
3. The losing side has a window to appeal — `file_appeal(case_id)`, staking 10 GEN.
   This freezes the evidence and opens a 5-juror commit-reveal round.
4. Jurors who registered (`register_as_juror()`) commit a hash of their vote,
   then reveal it once commitment closes.
5. If a majority (`votes * 2 > revealed_votes`) is reached, the case is
   `FINALIZED` and correct jurors split the appeal-stake reward pool.
6. If not, the case automatically escalates to a **new** 9-juror round on the
   same frozen evidence — up to 2 escalations (3 rounds total) before it's
   marked `FINALIZED_BY_DEADLOCK` and the accumulated pool is split across
   every juror who ever revealed.
7. Anyone owed a balance calls `claim()`.

## Repo layout

```
contracts/modappeal.py   the contract
frontend/index.html      no-build browser frontend (genlayer-js + MetaMask)
tests/                   offline test suite (21 tests, no external deps)
ARCHITECTURE.md          full design doc + GenVM lessons learned
```

## Running the tests

No dependencies to install — the suite uses a small local stub of the
GenLayer SDK (`tests/stub_genlayer.py`) and a plain custom runner:

```
python tests/run_tests.py
```

CI runs this on every push via `.github/workflows/tests.yml`.

## Using the frontend

Open `frontend/index.html` in a mobile browser with MetaMask (or any
injected-wallet browser). It talks directly to GenLayer Studio via
[`genlayer-js`](https://www.npmjs.com/package/genlayer-js) loaded from a CDN —
no build step, no server. The deployed contract address above is filled in by
default; paste a different address to point it at another instance.

The frontend covers every contract method: filing a flag, running the
automated verdict, filing an appeal, registering as a juror, committing and
revealing a vote (the commitment hash is computed client-side with
`crypto.subtle`, so no external tool is needed), closing a commit phase,
tallying a round, and claiming rewards.

## License

MIT — see [LICENSE](./LICENSE).
