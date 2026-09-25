# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import datetime
import hashlib
import json

VIOLATION = "VIOLATION"
NO_VIOLATION = "NO_VIOLATION"
PARTIAL = "PARTIAL"
VERDICTS = (VIOLATION, NO_VIOLATION, PARTIAL)

ST_AUTOMATED_VERDICT = "AUTOMATED_VERDICT"
ST_APPEALED = "APPEALED"
ST_JURY_COMMIT = "JURY_COMMIT"
ST_JURY_REVEAL = "JURY_REVEAL"
ST_FINALIZED = "FINALIZED"
ST_FINALIZED_BY_DEFAULT = "FINALIZED_BY_DEFAULT"
ST_FINALIZED_BY_DEADLOCK = "FINALIZED_BY_DEADLOCK"

BASE_JURY_SIZE = 5
ESCALATION_JURY_SIZE = 9
MAX_ESCALATIONS = 2  # up to 2 escalations beyond the base round -> 3 jury rounds max

APPEAL_STAKE = u256(10 * 10**18)
JUROR_STAKE = u256(2 * 10**18)

APPEAL_WINDOW_SECONDS = 3 * 24 * 60 * 60
COMMIT_WINDOW_SECONDS = 24 * 60 * 60
REVEAL_WINDOW_SECONDS = 24 * 60 * 60

ZERO_ADDRESS = Address("0x0000000000000000000000000000000000000000")


def jury_size_for_round(round_id: int) -> int:
    return BASE_JURY_SIZE if round_id == 1 else ESCALATION_JURY_SIZE


def has_majority(candidate_votes: int, revealed_votes: int) -> bool:
    return revealed_votes > 0 and candidate_votes * 2 > revealed_votes


@allow_storage
@dataclass
class Round:
    round_id: u256
    jury_size: u256
    jurors: DynArray[Address]
    commitments: TreeMap[Address, bytes]
    reveals: TreeMap[Address, str]
    revealed_count: u256
    vote_violation: u256
    vote_no_violation: u256
    vote_partial: u256
    commit_deadline: datetime.datetime
    reveal_deadline: datetime.datetime
    tallied: bool


@allow_storage
@dataclass
class Case:
    case_id: u256
    platform: Address
    flagger: Address
    publisher: Address
    content_id: str
    evidence_urls: DynArray[str]
    created_at: datetime.datetime
    status: str
    automated_verdict: str
    appeal_window_deadline: datetime.datetime
    appellant: Address
    evidence_hash: str
    escalation_count: u256
    current_round_id: u256
    used_jurors: DynArray[Address]
    reward_pool: u256
    final_verdict: str
    deadlock_eligible: DynArray[Address]


MODERATION_POLICY = """Content Moderation Policy (v1):
A post is VIOLATION if it directly threatens violence against a person or
group, contains hate speech targeting a protected characteristic (race,
religion, ethnicity, gender, sexual orientation, disability), or shares
content that sexually exploits minors.
A post is NO_VIOLATION if none of the above apply, even if the content is
controversial, offensive, or in poor taste.
A post is PARTIAL if it borders on a violation (e.g. hostile language
directed at a group without an explicit threat, or ambiguous edge-case
hate speech) such that a human reviewer would flag it for context, but it
does not unambiguously meet the VIOLATION bar above."""


def _fetch_evidence_content(evidence_urls: list) -> str:
    # Actually fetch the cited evidence rather than judging a bare URL
    # string. Each validator performs this fetch independently (see
    # _derive_verdict / the leader+validator wiring in auto_verdict),
    # so the verdict is grounded in fetched content, not a URL's name.
    # If EVERY evidence URL fails to fetch, there is no real evidence to
    # adjudicate on at all -- raise rather than let a verdict be issued
    # against a placeholder "[unable to fetch ...]" string. A per-URL
    # failure alongside at least one successful fetch is still tolerated
    # (partial evidence is still real evidence).
    chunks = []
    any_succeeded = False
    for url in evidence_urls:
        try:
            chunks.append(gl.nondet.web.render(url))
            any_succeeded = True
        except Exception:
            chunks.append(f"[unable to fetch {url}]")
    if not any_succeeded:
        raise Exception("no evidence could be fetched; refusing to issue a verdict")
    return "\n---\n".join(chunks)


def _derive_verdict(content_id: str, evidence_urls: list) -> str:
    fetched = _fetch_evidence_content(evidence_urls)
    prompt = (
        f"{MODERATION_POLICY}\n\n"
        f"Content id: {content_id}.\n"
        f"Fetched evidence content:\n{fetched}\n\n"
        "Apply the policy above strictly to the fetched evidence content "
        "above -- not to the URL or content id alone. Respond with exactly "
        "one word: VIOLATION, NO_VIOLATION, or PARTIAL."
    )
    result = gl.nondet.exec_prompt(prompt).strip().upper()
    return result if result in VERDICTS else NO_VIOLATION


class ModAppeal(gl.Contract):
    cases: TreeMap[u256, Case]
    rounds: TreeMap[str, Round]
    claimable_rewards: TreeMap[Address, u256]
    next_case_id: u256
    treasury: u256
    candidate_jury_pool: DynArray[Address]

    def __init__(self):
        self.next_case_id = u256(1)
        self.treasury = u256(0)

    def _round_key(self, case_id: int, round_id: int) -> str:
        return f"{case_id}:{round_id}"

    def _now(self) -> datetime.datetime:
        return datetime.datetime.now()

    @gl.public.write
    def register_as_juror(self):
        addr = gl.message.sender_address
        if addr not in self.candidate_jury_pool:
            self.candidate_jury_pool.append(addr)

    @gl.public.write
    def submit_flag(self, platform: str, flagger: str, publisher: str,
                     content_id: str, evidence_urls: list) -> u256:
        case_id = self.next_case_id
        self.next_case_id = u256(int(self.next_case_id) + 1)

        evidence_arr = list(evidence_urls)
        used_jurors_arr = []
        deadlock_arr = []

        now = self._now()
        case = Case(
            case_id=case_id,
            platform=Address(platform),
            flagger=Address(flagger),
            publisher=Address(publisher),
            content_id=content_id,
            evidence_urls=evidence_arr,
            created_at=now,
            status=ST_AUTOMATED_VERDICT,
            automated_verdict="",
            appeal_window_deadline=now,
            appellant=ZERO_ADDRESS,
            evidence_hash="",
            escalation_count=u256(0),
            current_round_id=u256(0),
            used_jurors=used_jurors_arr,
            reward_pool=u256(0),
            final_verdict="",
            deadlock_eligible=deadlock_arr,
        )
        self.cases[case_id] = case
        return case_id

    @gl.public.write
    def auto_verdict(self, case_id: u256):
        case = self.cases[case_id]
        if case.status != ST_AUTOMATED_VERDICT:
            raise Exception("wrong state")
        # Copy the needed fields OUT of storage before entering the nondet
        # block: a storage-backed reference used inside a leader/validator
        # closure is unsafe. This must be pure data with NO reference to
        # `self` at all -- a lambda that calls a bound method (self.foo(...))
        # captures `self` (the whole contract, storage fields included) in
        # its closure, which GenVM then tries to pickle for the sandboxed
        # nondet worker and fails ("Detected pickling storage class").
        content_id = str(case.content_id)
        evidence_urls = list(case.evidence_urls)

        def leader_fn():
            return _derive_verdict(content_id, evidence_urls)

        def validator_fn(leader_result):
            # Meaningful validator adjudication: each validator independently
            # re-fetches the cited evidence and re-derives its own verdict
            # against the same bound policy, then requires an exact match
            # with the leader's verdict -- not a bare format/label check.
            # Calls the module-level function directly (not through the
            # leader_fn closure above) so this closure only ever captures
            # plain values (content_id, evidence_urls), never another
            # closure -- matching the exact shape already proven safe to
            # serialize for the sandboxed nondet worker.
            if not isinstance(leader_result, gl.vm.Return):
                return False
            independent_verdict = _derive_verdict(content_id, evidence_urls)
            return independent_verdict == leader_result.calldata

        verdict = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        case.automated_verdict = verdict
        case.appeal_window_deadline = self._now() + datetime.timedelta(seconds=APPEAL_WINDOW_SECONDS)
        self.cases[case_id] = case

    @gl.public.write
    def expire_if_unappealed(self, case_id: u256):
        case = self.cases[case_id]
        if case.status != ST_AUTOMATED_VERDICT:
            raise Exception("wrong state")
        if self._now() < case.appeal_window_deadline:
            raise Exception("appeal window still open")
        case.status = ST_FINALIZED_BY_DEFAULT
        case.final_verdict = case.automated_verdict
        self.cases[case_id] = case

    def _select_jurors(self, case_id: int, available: list, size: int) -> list:
        # v1 limitation: deterministic, not truly random. But case-specific
        # (hash of case_id + address) rather than plain FIFO by registration
        # order, so no single early-registered address is always picked
        # across every case. Documented in ARCHITECTURE.md as a v1 trade-off;
        # sybil registration is still cheap (no juror-registration stake),
        # also documented as deferred to v2.
        def sort_key(addr):
            return hashlib.sha256(f"{case_id}:{addr}".encode()).hexdigest()
        return sorted(available, key=sort_key)[:size]

    def _start_round(self, case: Case):
        round_id = int(case.current_round_id) + 1
        size = jury_size_for_round(round_id)
        available = [a for a in self.candidate_jury_pool if a not in case.used_jurors]
        if len(available) < size:
            raise Exception("not enough eligible jurors registered")
        chosen = self._select_jurors(int(case.case_id), available, size)

        jurors_arr = list(chosen)
        for a in chosen:
            case.used_jurors.append(a)

        commitments = gl.storage.inmem_allocate(TreeMap[Address, bytes])
        reveals = gl.storage.inmem_allocate(TreeMap[Address, str])

        now = self._now()
        r = Round(
            round_id=u256(round_id),
            jury_size=u256(size),
            jurors=jurors_arr,
            commitments=commitments,
            reveals=reveals,
            revealed_count=u256(0),
            vote_violation=u256(0),
            vote_no_violation=u256(0),
            vote_partial=u256(0),
            commit_deadline=now + datetime.timedelta(seconds=COMMIT_WINDOW_SECONDS),
            reveal_deadline=now + datetime.timedelta(seconds=COMMIT_WINDOW_SECONDS + REVEAL_WINDOW_SECONDS),
            tallied=False,
        )
        self.rounds[self._round_key(int(case.case_id), round_id)] = r
        case.current_round_id = u256(round_id)
        case.status = ST_JURY_COMMIT

    @gl.public.write.payable
    def file_appeal(self, case_id: u256):
        case = self.cases[case_id]
        if case.status != ST_AUTOMATED_VERDICT:
            raise Exception("wrong state")
        if self._now() > case.appeal_window_deadline:
            raise Exception("appeal window closed")
        sender = gl.message.sender_address
        if case.automated_verdict == NO_VIOLATION:
            if sender != case.flagger:
                raise Exception("only flagger may appeal a NO_VIOLATION verdict")
        elif case.automated_verdict == PARTIAL:
            if sender != case.flagger and sender != case.publisher:
                raise Exception("only flagger or publisher may appeal a PARTIAL verdict")
        else:  # VIOLATION
            if sender != case.publisher:
                raise Exception("only publisher may appeal a VIOLATION verdict")
        if gl.message.value != APPEAL_STAKE:
            raise Exception("incorrect appeal stake")
        case.appellant = sender
        case.reward_pool = u256(int(case.reward_pool) + int(APPEAL_STAKE))
        case.evidence_hash = hashlib.sha256(
            json.dumps(list(case.evidence_urls), sort_keys=True).encode()
        ).hexdigest()
        case.status = ST_APPEALED
        self._start_round(case)
        self.cases[case_id] = case

    @gl.public.write.payable
    def commit_vote(self, case_id: u256, round_id: u256, commitment_hash: str):
        case = self.cases[case_id]
        rk = self._round_key(int(case_id), int(round_id))
        r = self.rounds[rk]
        if case.status != ST_JURY_COMMIT or int(round_id) != int(case.current_round_id):
            raise Exception("wrong round or state")
        if self._now() > r.commit_deadline:
            raise Exception("commit window closed")
        sender = gl.message.sender_address
        if sender not in r.jurors:
            raise Exception("not a juror for this round")
        if sender in r.commitments:
            raise Exception("already committed")
        if gl.message.value != JUROR_STAKE:
            raise Exception("incorrect juror stake")
        r.commitments[sender] = bytes.fromhex(commitment_hash)
        self.rounds[rk] = r

    @gl.public.write
    def close_commit(self, case_id: u256, round_id: u256):
        case = self.cases[case_id]
        rk = self._round_key(int(case_id), int(round_id))
        r = self.rounds[rk]
        if int(round_id) != int(case.current_round_id) or case.status != ST_JURY_COMMIT:
            raise Exception("wrong round or state")
        if self._now() <= r.commit_deadline:
            raise Exception("commit window still open")
        case.status = ST_JURY_REVEAL
        self.cases[case_id] = case

    @gl.public.write
    def reveal_vote(self, case_id: u256, round_id: u256, verdict: str, salt: str):
        case = self.cases[case_id]
        if int(round_id) != int(case.current_round_id):
            raise Exception("stale round: reveals only accepted for the current round")
        rk = self._round_key(int(case_id), int(round_id))
        r = self.rounds[rk]
        if case.status != ST_JURY_REVEAL:
            raise Exception("wrong state")
        if self._now() > r.reveal_deadline:
            raise Exception("reveal window closed")
        sender = gl.message.sender_address
        if sender not in r.commitments:
            raise Exception("no commitment found")
        if sender in r.reveals:
            raise Exception("already revealed")
        if verdict not in VERDICTS:
            raise Exception("invalid verdict")
        expected = hashlib.sha256((verdict + salt).encode()).digest()
        if expected != r.commitments[sender]:
            raise Exception("reveal does not match commitment")
        r.reveals[sender] = verdict
        r.revealed_count = u256(int(r.revealed_count) + 1)
        if verdict == VIOLATION:
            r.vote_violation = u256(int(r.vote_violation) + 1)
        elif verdict == NO_VIOLATION:
            r.vote_no_violation = u256(int(r.vote_no_violation) + 1)
        else:
            r.vote_partial = u256(int(r.vote_partial) + 1)
        if sender not in case.deadlock_eligible:
            case.deadlock_eligible.append(sender)
        self.rounds[rk] = r
        self.cases[case_id] = case

    def _slash_non_reveals(self, case: Case, r: Round):
        for juror in r.jurors:
            if juror not in r.reveals:
                if juror in r.commitments:
                    case.reward_pool = u256(int(case.reward_pool) + int(JUROR_STAKE))

    def _credit(self, address: Address, amount: int):
        current = int(self.claimable_rewards[address]) if address in self.claimable_rewards else 0
        self.claimable_rewards[address] = u256(current + amount)

    @gl.public.write
    def tally_round(self, case_id: u256, round_id: u256):
        case = self.cases[case_id]
        if int(round_id) != int(case.current_round_id) or case.status != ST_JURY_REVEAL:
            raise Exception("wrong round or state")
        rk = self._round_key(int(case_id), int(round_id))
        r = self.rounds[rk]
        if self._now() <= r.reveal_deadline:
            raise Exception("reveal window still open")
        if r.tallied:
            raise Exception("already tallied")
        r.tallied = True

        self._slash_non_reveals(case, r)

        counts = {
            VIOLATION: int(r.vote_violation),
            NO_VIOLATION: int(r.vote_no_violation),
            PARTIAL: int(r.vote_partial),
        }
        winner = None
        for v in VERDICTS:
            if has_majority(counts[v], int(r.revealed_count)):
                winner = v
                break

        if winner is not None:
            correct = [addr for addr, vote in r.reveals.items() if vote == winner]
            minority = [addr for addr, vote in r.reveals.items() if vote != winner]
            pool = int(case.reward_pool)
            n_correct = len(correct)
            reward_each = pool // n_correct if n_correct > 0 else 0
            remainder = pool - reward_each * n_correct
            for addr in correct:
                self._credit(addr, int(JUROR_STAKE) + reward_each)
            for addr in minority:
                self._credit(addr, int(JUROR_STAKE))
            self.treasury = u256(int(self.treasury) + remainder)
            case.reward_pool = u256(0)
            case.status = ST_FINALIZED
            case.final_verdict = winner
        else:
            for addr, vote in r.reveals.items():
                self._credit(addr, int(JUROR_STAKE))
            case.escalation_count = u256(int(case.escalation_count) + 1)
            if int(case.escalation_count) > MAX_ESCALATIONS:
                pool = int(case.reward_pool)
                eligible = list(case.deadlock_eligible)
                n = len(eligible)
                each = pool // n if n > 0 else 0
                remainder = pool - each * n
                for addr in eligible:
                    self._credit(addr, each)
                self.treasury = u256(int(self.treasury) + remainder)
                case.reward_pool = u256(0)
                case.status = ST_FINALIZED_BY_DEADLOCK
            else:
                self._start_round(case)

        self.rounds[rk] = r
        self.cases[case_id] = case

    @gl.public.write
    def claim(self):
        sender = gl.message.sender_address
        amount = int(self.claimable_rewards[sender]) if sender in self.claimable_rewards else 0
        if amount <= 0:
            raise Exception("nothing to claim")
        self.claimable_rewards[sender] = u256(0)
        gl.get_contract_at(sender).emit_transfer(value=u256(amount))

    @gl.public.write.payable
    def __on_errored_message__(self):
        # If a claim payout bounces back (e.g. the recipient is a contract
        # that rejects the transfer), restore the claimant's balance instead
        # of silently losing track of the funds.
        failed_recipient = gl.message.sender_address
        failed_value = gl.message.value
        self._credit(failed_recipient, int(failed_value))

    @gl.public.view
    def get_case(self, case_id: u256) -> dict:
        case = self.cases[case_id]
        return {
            "status": case.status,
            "automated_verdict": case.automated_verdict,
            "final_verdict": case.final_verdict,
            "escalation_count": int(case.escalation_count),
            "current_round_id": int(case.current_round_id),
            "reward_pool": int(case.reward_pool),
            "evidence_hash": case.evidence_hash,
        }

    @gl.public.view
    def get_round(self, case_id: u256, round_id: u256) -> dict:
        r = self.rounds[self._round_key(int(case_id), int(round_id))]
        return {
            "jury_size": int(r.jury_size),
            "jurors": [str(a) for a in r.jurors],
            "revealed_count": int(r.revealed_count),
            "vote_counts": {
                VIOLATION: int(r.vote_violation),
                NO_VIOLATION: int(r.vote_no_violation),
                PARTIAL: int(r.vote_partial),
            },
            "tallied": r.tallied,
        }

    @gl.public.view
    def get_claimable(self, address: str) -> u256:
        addr = Address(address)
        return self.claimable_rewards[addr] if addr in self.claimable_rewards else u256(0)
