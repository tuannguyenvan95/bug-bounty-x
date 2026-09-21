# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json


def _addr_str(addr: Address) -> str:
    """Safely format an Address instance into a lowercase hex string."""
    try:
        return addr.as_hex.lower()
    except Exception:
        return str(addr).lower()


def _get_sender() -> Address:
    """Safely obtain transaction sender across GenVM builds."""
    try:
        return gl.message.sender
    except Exception:
        try:
            return gl.message.sender_address
        except Exception:
            raise gl.UserError("Cannot resolve sender address.")


def _extract_repo_slug(url: str) -> str:
    """Extract normalized repository slug (e.g. github.com/owner/repo) from URL."""
    clean = url.strip().lower()
    if clean.startswith("https://"):
        clean = clean[8:]
    elif clean.startswith("http://"):
        clean = clean[7:]
    parts = clean.strip("/").split("/")
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}/{parts[2]}"
    return clean.strip("/")


@allow_storage
@dataclass
class BountyPool:
    """Storage struct representing a security bounty pool for a repository."""
    pool_id: str
    creator: Address
    repo_url: str
    repo_slug: str
    total_deposited: bigint
    p0_critical_amount: bigint  # Fund drain / remote code execution
    p1_high_amount: bigint      # Logic break / state freeze
    p2_medium_amount: bigint    # Griefing / edge-case leak
    is_active: bool


@allow_storage
@dataclass
class BountyClaim:
    """Storage struct representing a submitted vulnerability fix claim."""
    claim_id: str
    pool_id: str
    hacker: Address
    pr_diff_url: str
    issue_url: str
    status: str          # "PENDING", "APPROVED", "REJECTED"
    severity_tier: str   # "P0", "P1", "P2", "REJECTED"
    reward_awarded: bigint
    reason: str          # LLM incident response rationale
    created_at: bigint
    resolved_at: bigint


class Contract(gl.Contract):
    """
    BugBountyX: Autonomous GitHub Bug Bounty Severity & Payout Protocol
    Track: Future of Work
    """
    owner: Address
    pool_count: bigint
    claim_count: bigint
    pools: TreeMap[str, BountyPool]
    claims: TreeMap[str, BountyClaim]
    claimed_patches: TreeMap[str, bool]
    pending_patches: TreeMap[str, bool]

    def __init__(self):
        # GenVM automatically initializes TreeMap storage fields.
        # DO NOT reassign self.pools = TreeMap() here to prevent AssertionError.
        self.owner = _get_sender()
        self.pool_count = bigint(0)
        self.claim_count = bigint(0)

    def _parse_llm_json(self, text: str) -> dict:
        """Safely parse LLM responses, stripping markdown wrappers if present."""
        try:
            cleaned = str(text).strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            return json.loads(cleaned.strip())
        except Exception as e:
            return {
                "tier": "REJECTED",
                "confidence": 0,
                "reason": f"Failed to parse LLM JSON output: {str(e)[:100]}"
            }

    @gl.public.write.payable
    def create_bounty_pool(
        self,
        repo_url: str,
        p0_critical: int,
        p1_high: int,
        p2_medium: int
    ) -> str:
        """
        Project teams lock funds into an escrow bounty pool and define payout brackets.
        Amounts are passed in GEN (wei format).
        """
        deposit = bigint(gl.message.value)
        if deposit <= bigint(0):
            raise gl.UserError("Initial escrow deposit must be greater than 0 GEN.")

        p0 = bigint(p0_critical)
        p1 = bigint(p1_high)
        p2 = bigint(p2_medium)

        if p0 <= bigint(0) or p1 <= bigint(0) or p2 <= bigint(0):
            raise gl.UserError("Bounty tiers must be greater than 0.")

        clean_repo = repo_url.strip().rstrip("/")
        if not clean_repo.startswith("http://") and not clean_repo.startswith("https://"):
            raise gl.UserError("repo_url must start with http:// or https://")

        repo_slug = _extract_repo_slug(clean_repo)

        self.pool_count += bigint(1)
        pid = str(self.pool_count)

        self.pools[pid] = BountyPool(
            pool_id=pid,
            creator=_get_sender(),
            repo_url=clean_repo,
            repo_slug=repo_slug,
            total_deposited=deposit,
            p0_critical_amount=p0,
            p1_high_amount=p1,
            p2_medium_amount=p2,
            is_active=True
        )

        return pid

    @gl.public.write.payable
    def top_up_pool(self, pool_id: str) -> None:
        """Deposit additional funds into an existing bounty pool."""
        if pool_id not in self.pools:
            raise gl.UserError("Pool not found.")

        deposit = bigint(gl.message.value)
        if deposit <= bigint(0):
            raise gl.UserError("Top up amount must be greater than 0 GEN.")

        pool = self.pools[pool_id]
        pool.total_deposited += deposit
        self.pools[pool_id] = pool

    @gl.public.write
    def submit_claim(self, pool_id: str, pr_diff_url: str, issue_url: str) -> str:
        """
        Whitehat hacker submits a claim containing the PR diff URL and issue URL.
        Binds the claim strictly to the pool's configured repository and enforces persistent replay protection.
        """
        if pool_id not in self.pools:
            raise gl.UserError("Pool not found.")

        pool = self.pools[pool_id]
        if not pool.is_active:
            raise gl.UserError("Bounty pool is inactive.")

        clean_pr = pr_diff_url.strip()
        clean_issue = issue_url.strip()

        if not clean_pr.startswith("http://") and not clean_pr.startswith("https://"):
            raise gl.UserError("pr_diff_url must begin with http:// or https://")

        if not clean_issue.startswith("http://") and not clean_issue.startswith("https://"):
            raise gl.UserError("issue_url must begin with http:// or https://")

        # 1. Repository Binding: Enforce that PR and Issue belong strictly to configured repository
        repo_slug = pool.repo_slug.lower()
        if repo_slug not in clean_pr.lower():
            raise gl.UserError(f"pr_diff_url does not belong to configured repository: {pool.repo_slug}")

        if repo_slug not in clean_issue.lower():
            raise gl.UserError(f"issue_url does not belong to configured repository: {pool.repo_slug}")

        # 2. Auto-normalize GitHub pull request web URLs to raw diff endpoints
        if "github.com/" in clean_pr and "/pull/" in clean_pr:
            pr_base = clean_pr.rstrip("/")
            if not pr_base.endswith(".diff") and not pr_base.endswith(".patch"):
                clean_pr = f"{pr_base}.diff"

        # 3. Persistent Replay Protection: Check if PR/patch has already received payout or is pending
        patch_key = f"{pool_id}:{clean_pr.lower()}"
        if patch_key in self.claimed_patches and self.claimed_patches[patch_key]:
            raise gl.UserError("This PR or patch has already received a bounty payout.")

        if patch_key in self.pending_patches and self.pending_patches[patch_key]:
            raise gl.UserError("A claim for this PR or patch is already pending adjudication.")

        self.pending_patches[patch_key] = True

        self.claim_count += bigint(1)
        cid = str(self.claim_count)

        self.claims[cid] = BountyClaim(
            claim_id=cid,
            pool_id=pool_id,
            hacker=_get_sender(),
            pr_diff_url=clean_pr,
            issue_url=clean_issue,
            status="PENDING",
            severity_tier="PENDING",
            reward_awarded=bigint(0),
            reason="Awaiting decentralized AI Code Review consensus.",
            created_at=self.claim_count,
            resolved_at=bigint(0)
        )

        return cid

    @gl.public.write
    def adjudicate_claim(self, claim_id: str) -> None:
        """
        Consensus leader & validators fetch the PR patch/diff, analyze the vulnerability
        and fix severity, verify Issue security context, and resolve the payout.
        """
        if claim_id not in self.claims:
            raise gl.UserError("Claim not found.")

        claim = self.claims[claim_id]
        if claim.status != "PENDING":
            raise gl.UserError("Claim already settled.")

        pool = self.pools[claim.pool_id]
        pr_url_local = str(claim.pr_diff_url)
        issue_url_local = str(claim.issue_url)
        repo_slug_local = str(pool.repo_slug)
        repo_url_local = str(pool.repo_url)

        def leader_fn():
            # 1. Fetch PR diff directly on-chain
            diff_text = ""
            diff_fetch_error = False
            try:
                res_diff = gl.nondet.web.render(pr_url_local, mode="text")
                diff_text = res_diff.content if hasattr(res_diff, "content") else str(res_diff)
            except Exception:
                diff_fetch_error = True

            if diff_fetch_error or not diff_text or len(diff_text.strip()) < 15:
                return {
                    "tier": "REJECTED",
                    "confidence": 100,
                    "reason": "Unable to fetch or render PR diff URL."
                }

            lower_diff = diff_text[:500].lower()
            if any(err in lower_diff for err in ["404 not found", "error 404", "access denied"]):
                return {
                    "tier": "REJECTED",
                    "confidence": 100,
                    "reason": "PR URL returned 404 Not Found or Access Denied."
                }

            # 2. Fetch and verify Issue security context directly on-chain
            issue_text = ""
            issue_fetch_error = False
            try:
                res_issue = gl.nondet.web.render(issue_url_local, mode="text")
                issue_text = res_issue.content if hasattr(res_issue, "content") else str(res_issue)
            except Exception:
                issue_fetch_error = True

            if issue_fetch_error or not issue_text or len(issue_text.strip()) < 15:
                return {
                    "tier": "REJECTED",
                    "confidence": 100,
                    "reason": "Unable to fetch or verify Issue security context URL."
                }

            lower_issue = issue_text[:500].lower()
            if any(err in lower_issue for err in ["404 not found", "error 404", "access denied"]):
                return {
                    "tier": "REJECTED",
                    "confidence": 100,
                    "reason": "Issue URL returned 404 Not Found or Access Denied."
                }

            truncated_diff = diff_text[:3500]
            truncated_issue = issue_text[:2000]

            # 3. Build Code Security Audit & Issue Context Prompt
            prompt = f"""You are a Lead Smart Contract Security Auditor on the GenLayer decentralized consensus network.
Evaluate the following verified pull request code diff and verified security issue details to determine whether the patch successfully fixes a valid security vulnerability in the configured repository, and assign a severity tier.

REPOSITORY: {repo_url_local} ({repo_slug_local})
PR URL: {pr_url_local}
ISSUE REFERENCE: {issue_url_local}

VERIFIED SECURITY ISSUE CONTEXT:
\"\"\"
{truncated_issue}
\"\"\"

VERIFIED CODE DIFF / PATCH CONTENT:
\"\"\"
{truncated_diff}
\"\"\"

SEVERITY GUIDELINES:
- P0: Critical severity. Direct fund theft, reentrancy drain, infinite minting, authentication bypass, or protocol collapse.
- P1: High severity. Partial lock of funds, denial of service requiring hardfork, severe state corruption, or oracle manipulation.
- P2: Medium severity. Griefing vectors, gas optimization bugs, minor logic inconsistency, unhandled exceptions without total fund loss.
- REJECTED: Cosmetic refactoring, documentation fixes, invalid patches, spam, or changes that do not resolve the verified security issue context.

RESPONSE FORMAT:
Respond ONLY with a VALID JSON object (no markdown, no backticks):
{{
  "tier": "P0" | "P1" | "P2" | "REJECTED",
  "confidence": <0-100>,
  "reason": "<clear explanation max 220 chars>"
}}"""

            try:
                raw_res = gl.nondet.exec_prompt(prompt, response_format="json")
                parsed = None
                if isinstance(raw_res, dict):
                    parsed = raw_res
                elif hasattr(raw_res, "content") and isinstance(raw_res.content, dict):
                    parsed = raw_res.content
                else:
                    text = raw_res.content if hasattr(raw_res, "content") else str(raw_res)
                    cleaned = str(text).strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    elif cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    parsed = json.loads(cleaned.strip())

                tier = str(parsed.get("tier", "REJECTED")).strip().upper()
                if tier not in ("P0", "P1", "P2", "REJECTED"):
                    tier = "REJECTED"

                try:
                    conf = int(parsed.get("confidence", 0))
                    conf = max(0, min(100, conf))
                except Exception:
                    conf = 50

                reason_str = str(parsed.get("reason", "Audited by decentralized AI security committee."))[:220]

                # Downgrade if AI confidence is weak
                if tier in ("P0", "P1") and conf < 65:
                    tier = "P2"
                    reason_str = f"[Confidence: {conf}%] Downgraded: " + reason_str

                return {
                    "tier": tier,
                    "confidence": conf,
                    "reason": reason_str
                }
            except Exception as e:
                return {
                    "tier": "REJECTED",
                    "confidence": 0,
                    "reason": f"AI audit execution failed: {str(e)[:100]}"
                }

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            leader = leader_res.calldata
            if not isinstance(leader, dict) or "tier" not in leader:
                return False

            mine = leader_fn()
            # CRITICAL RULE: Compare semantic TIER ONLY!
            # Ignore natural language variation in the 'reason' field.
            t_mine = str(mine.get("tier", "")).strip().upper()
            t_leader = str(leader.get("tier", "")).strip().upper()
            return t_mine == t_leader

        adjudication_res = gl.vm.run_nondet(leader_fn, validator_fn)
        if isinstance(adjudication_res, dict):
            final_res = adjudication_res
        else:
            final_res = self._parse_llm_json(str(adjudication_res))

        tier = str(final_res.get("tier", "REJECTED")).strip().upper()
        if tier not in ("P0", "P1", "P2", "REJECTED"):
            tier = "REJECTED"

        reason = str(final_res.get("reason", "Consensus concluded."))

        claim.severity_tier = tier
        claim.reason = reason
        claim.resolved_at = self.claim_count

        patch_key = f"{claim.pool_id}:{claim.pr_diff_url.lower()}"

        if tier == "REJECTED":
            claim.status = "REJECTED"
            claim.reward_awarded = bigint(0)
            self.claims[claim_id] = claim
            # Release pending lock so another fix or claim can be submitted
            self.pending_patches[patch_key] = False
            return

        # Determine payout based on tier
        payout = bigint(0)
        if tier == "P0":
            payout = pool.p0_critical_amount
        elif tier == "P1":
            payout = pool.p1_high_amount
        elif tier == "P2":
            payout = pool.p2_medium_amount

        # Check solvency of pool
        if payout > pool.total_deposited:
            payout = pool.total_deposited  # Pay maximum available remaining

        if payout > bigint(0):
            pool.total_deposited -= payout
            claim.status = "APPROVED"
            claim.reward_awarded = payout

            self.pools[claim.pool_id] = pool
            self.claims[claim_id] = claim

            # Persistent Replay Protection: Lock patch permanently against any future payouts
            self.claimed_patches[patch_key] = True
            self.pending_patches[patch_key] = False

            # Disburse bounty directly to whitehat hacker (cast to u256)
            gl.get_contract_at(claim.hacker).emit_transfer(value=u256(payout))
        else:
            claim.status = "REJECTED"
            claim.reason = "Pool has insufficient funds for bounty payout."
            self.claims[claim_id] = claim
            self.pending_patches[patch_key] = False

    @gl.public.write
    def toggle_pool_status(self, pool_id: str, is_active: bool) -> None:
        """Allow pool creator or contract owner to pause/unpause bounty acceptance."""
        if pool_id not in self.pools:
            raise gl.UserError("Pool not found.")
        pool = self.pools[pool_id]
        sender_hex = _addr_str(_get_sender())
        if sender_hex != _addr_str(pool.creator) and sender_hex != _addr_str(self.owner):
            raise gl.UserError("Unauthorized: Only creator or owner can toggle status.")
        pool.is_active = is_active
        self.pools[pool_id] = pool

    @gl.public.view
    def get_pool(self, pool_id: str) -> str:
        """Retrieve details of a bounty pool as a JSON string."""
        if pool_id not in self.pools:
            raise gl.UserError("Pool not found.")
        p = self.pools[pool_id]
        return json.dumps({
            "pool_id": p.pool_id,
            "creator": _addr_str(p.creator),
            "repo_url": p.repo_url,
            "repo_slug": p.repo_slug,
            "total_deposited": str(p.total_deposited),
            "p0_critical": str(p.p0_critical_amount),
            "p1_high": str(p.p1_high_amount),
            "p2_medium": str(p.p2_medium_amount),
            "is_active": p.is_active
        })

    @gl.public.view
    def get_claim(self, claim_id: str) -> str:
        """Retrieve details of a claim as a JSON string."""
        if claim_id not in self.claims:
            raise gl.UserError("Claim not found.")
        c = self.claims[claim_id]
        return json.dumps({
            "claim_id": c.claim_id,
            "pool_id": c.pool_id,
            "hacker": _addr_str(c.hacker),
            "pr_diff_url": c.pr_diff_url,
            "issue_url": c.issue_url,
            "status": c.status,
            "severity_tier": c.severity_tier,
            "reward_awarded": str(c.reward_awarded),
            "reason": c.reason,
            "created_at": str(c.created_at),
            "resolved_at": str(c.resolved_at)
        })

    @gl.public.view
    def is_patch_claimed(self, pool_id: str, pr_diff_url: str) -> bool:
        """Check whether a specific PR/patch has already received a bounty payout."""
        clean_pr = pr_diff_url.strip()
        if "github.com/" in clean_pr and "/pull/" in clean_pr:
            pr_base = clean_pr.rstrip("/")
            if not pr_base.endswith(".diff") and not pr_base.endswith(".patch"):
                clean_pr = f"{pr_base}.diff"
        patch_key = f"{pool_id}:{clean_pr.lower()}"
        return patch_key in self.claimed_patches and self.claimed_patches[patch_key]

    @gl.public.view
    def is_patch_pending(self, pool_id: str, pr_diff_url: str) -> bool:
        """Check whether a specific PR/patch is currently pending adjudication."""
        clean_pr = pr_diff_url.strip()
        if "github.com/" in clean_pr and "/pull/" in clean_pr:
            pr_base = clean_pr.rstrip("/")
            if not pr_base.endswith(".diff") and not pr_base.endswith(".patch"):
                clean_pr = f"{pr_base}.diff"
        patch_key = f"{pool_id}:{clean_pr.lower()}"
        return patch_key in self.pending_patches and self.pending_patches[patch_key]

    @gl.public.view
    def get_pool_count(self) -> int:
        return int(self.pool_count)

    @gl.public.view
    def get_claim_count(self) -> int:
        return int(self.claim_count)
