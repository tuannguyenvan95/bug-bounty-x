import pytest
from gltest import *


@pytest.fixture
def contract(direct_deploy):
    return direct_deploy("contracts/bug_bounty_x.py")


def test_initial_state(contract):
    assert contract.get_pool_count() == 0
    assert contract.get_claim_count() == 0


def test_create_pool_success(contract, direct_vm, direct_alice):
    direct_vm.sender = direct_alice
    direct_vm.value = 10000  # 10,000 GEN

    pool_id = contract.create_bounty_pool(
        "https://github.com/defi-protocol/core-contracts",
        5000,  # P0 Critical
        2500,  # P1 High
        1000   # P2 Medium
    )

    assert str(pool_id) == "1"
    assert contract.get_pool_count() == 1

    pool_data = contract.get_pool(pool_id)
    assert '"pool_id": "1"' in pool_data
    assert '"repo_url": "https://github.com/defi-protocol/core-contracts"' in pool_data
    assert '"repo_slug": "github.com/defi-protocol/core-contracts"' in pool_data
    assert '"total_deposited": "10000"' in pool_data
    assert '"p0_critical": "5000"' in pool_data
    assert '"p1_high": "2500"' in pool_data
    assert '"p2_medium": "1000"' in pool_data
    assert '"is_active": true' in pool_data


def test_create_pool_invalid_inputs(contract, direct_vm, direct_alice):
    direct_vm.sender = direct_alice

    # Zero deposit should fail
    direct_vm.value = 0
    with pytest.raises(Exception):
        contract.create_bounty_pool("https://github.com/org/repo", 500, 250, 100)

    # Invalid URL scheme
    direct_vm.value = 5000
    with pytest.raises(Exception):
        contract.create_bounty_pool("ftp://github.com/org/repo", 500, 250, 100)

    # Zero tier amount
    with pytest.raises(Exception):
        contract.create_bounty_pool("https://github.com/org/repo", 0, 250, 100)


def test_top_up_pool(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 2000, 1000, 500)

    # Bob tops up pool with 3000 GEN
    direct_vm.sender = direct_bob
    direct_vm.value = 3000
    contract.top_up_pool(pool_id)

    pool_data = contract.get_pool(pool_id)
    assert '"total_deposited": "8000"' in pool_data


def test_submit_claim_success(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 5000, 2000, 500)

    direct_vm.sender = direct_bob
    claim_id = contract.submit_claim(
        pool_id,
        "https://github.com/org/repo/pull/42.diff",
        "https://github.com/org/repo/issues/41"
    )

    assert str(claim_id) == "1"
    assert contract.get_claim_count() == 1

    claim_data = contract.get_claim(claim_id)
    assert '"claim_id": "1"' in claim_data
    assert '"pool_id": "1"' in claim_data
    assert '"status": "PENDING"' in claim_data
    assert '"severity_tier": "PENDING"' in claim_data
    assert '"reward_awarded": "0"' in claim_data
    assert contract.is_patch_pending(pool_id, "https://github.com/org/repo/pull/42.diff") is True
    assert contract.is_patch_claimed(pool_id, "https://github.com/org/repo/pull/42.diff") is False


def test_github_pr_url_auto_normalized(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 2000, 1000, 500)

    direct_vm.sender = direct_bob
    # Whitehat passes regular web PR url without .diff
    claim_id = contract.submit_claim(
        pool_id,
        "https://github.com/org/repo/pull/123",
        "https://github.com/org/repo/issues/122"
    )

    claim_data = contract.get_claim(claim_id)
    # Must be auto-normalized with .diff appended!
    assert "https://github.com/org/repo/pull/123.diff" in claim_data


def test_repository_binding_rejects_mismatched_repo(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/trusted-org/vault-core", 2000, 1000, 500)

    direct_vm.sender = direct_bob

    # Attempt to submit PR from a foreign repository
    with pytest.raises(Exception):
        contract.submit_claim(
            pool_id,
            "https://github.com/attacker-org/malicious-repo/pull/1.diff",
            "https://github.com/trusted-org/vault-core/issues/1"
        )

    # Attempt to submit Issue from a foreign repository
    with pytest.raises(Exception):
        contract.submit_claim(
            pool_id,
            "https://github.com/trusted-org/vault-core/pull/1.diff",
            "https://github.com/attacker-org/malicious-repo/issues/1"
        )


def test_persistent_replay_protection_blocks_duplicate_claim(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 5000, 2000, 500)

    direct_vm.sender = direct_bob
    pr_url = "https://github.com/org/repo/pull/55"
    issue_url = "https://github.com/org/repo/issues/54"

    # First claim submission succeeds
    contract.submit_claim(pool_id, pr_url, issue_url)

    # Second claim submission of the exact same PR while first is pending MUST FAIL
    with pytest.raises(Exception):
        contract.submit_claim(pool_id, pr_url, issue_url)


def test_adjudicate_p0_critical_approved_and_replay_locked(contract, direct_vm, direct_alice, direct_bob):
    # Alice creates pool
    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 6000, 3000, 1000)

    # Bob (whitehat) submits claim
    direct_vm.sender = direct_bob
    pr_url = "https://github.com/org/repo/pull/99.diff"
    issue_url = "https://github.com/org/repo/issues/99"
    claim_id = contract.submit_claim(pool_id, pr_url, issue_url)

    # Mock web render for BOTH PR diff and Issue context
    diff_content = """diff --git a/Vault.sol b/Vault.sol
index 1234567..89abcdef 100644
--- a/Vault.sol
+++ b/Vault.sol
@@ -20,6 +20,7 @@ contract Vault {
+    bool private _locked;
     function withdraw(uint256 amount) external nonReentrant {
"""
    issue_content = "Issue #99: Critical reentrancy vulnerability in withdraw function allows fund drainage."

    direct_vm.mock_web("pull/99.diff", {"status": 200, "body": diff_content})
    direct_vm.mock_web("issues/99", {"status": 200, "body": issue_content})
    direct_vm.mock_llm(".*", '{"tier": "P0", "confidence": 98, "reason": "Patched critical reentrancy drain bug in withdraw function."}')

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "APPROVED"' in claim_data
    assert '"severity_tier": "P0"' in claim_data
    assert '"reward_awarded": "6000"' in claim_data

    # Pool funds reduced by 6000
    pool_data = contract.get_pool(pool_id)
    assert '"total_deposited": "4000"' in pool_data

    # Replay protection: PR is now marked as claimed
    assert contract.is_patch_claimed(pool_id, pr_url) is True
    assert contract.is_patch_pending(pool_id, pr_url) is False

    # Attempting to re-submit or replay the same PR for another bounty payout MUST FAIL
    with pytest.raises(Exception):
        contract.submit_claim(pool_id, pr_url, issue_url)


def test_adjudicate_p1_high_approved(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 8000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 5000, 2000, 500)

    direct_vm.sender = direct_bob
    pr_url = "https://github.com/org/repo/pull/100.diff"
    issue_url = "https://github.com/org/repo/issues/100"
    claim_id = contract.submit_claim(pool_id, pr_url, issue_url)

    direct_vm.mock_web("pull/100.diff", {"status": 200, "body": "diff --git a/Staking.sol: fixed lockup overflow"})
    direct_vm.mock_web("issues/100", {"status": 200, "body": "Issue #100: Staking contract locks user funds indefinitely upon overflow."})
    direct_vm.mock_llm(".*", '{"tier": "P1", "confidence": 85, "reason": "Fixes staking freeze issue causing DoS on claims."}')

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "APPROVED"' in claim_data
    assert '"severity_tier": "P1"' in claim_data
    assert '"reward_awarded": "2000"' in claim_data

    pool_data = contract.get_pool(pool_id)
    assert '"total_deposited": "6000"' in pool_data


def test_adjudicate_rejected_cosmetic_diff(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 2000, 1000, 500)

    direct_vm.sender = direct_bob
    pr_url = "https://github.com/org/repo/pull/101.diff"
    issue_url = "https://github.com/org/repo/issues/101"
    claim_id = contract.submit_claim(pool_id, pr_url, issue_url)

    direct_vm.mock_web("pull/101.diff", {"status": 200, "body": "diff --git a/README.md: fixed typo in docs"})
    direct_vm.mock_web("issues/101", {"status": 200, "body": "Issue #101: Typo in documentation example."})
    direct_vm.mock_llm(".*", '{"tier": "REJECTED", "confidence": 95, "reason": "Documentation cosmetic change, not a security vulnerability."}')

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "REJECTED"' in claim_data
    assert '"severity_tier": "REJECTED"' in claim_data
    assert '"reward_awarded": "0"' in claim_data

    # Pool funds untouched
    pool_data = contract.get_pool(pool_id)
    assert '"total_deposited": "5000"' in pool_data


def test_adjudicate_rejected_if_issue_404(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 2000, 1000, 500)

    direct_vm.sender = direct_bob
    pr_url = "https://github.com/org/repo/pull/105.diff"
    issue_url = "https://github.com/org/repo/issues/404"
    claim_id = contract.submit_claim(pool_id, pr_url, issue_url)

    direct_vm.mock_web("pull/105.diff", {"status": 200, "body": "diff --git a/Token.sol: some change"})
    # Issue returns 404
    direct_vm.mock_web("issues/404", {"status": 404, "body": "404 Not Found"})

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "REJECTED"' in claim_data
    assert "Issue" in claim_data


def test_adjudicate_low_confidence_downgrades_to_p2(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 3000, 1500, 400)

    direct_vm.sender = direct_bob
    pr_url = "https://github.com/org/repo/pull/102.diff"
    issue_url = "https://github.com/org/repo/issues/102"
    claim_id = contract.submit_claim(pool_id, pr_url, issue_url)

    direct_vm.mock_web("pull/102.diff", {"status": 200, "body": "diff --git a/Math.sol: potential rounding issue"})
    direct_vm.mock_web("issues/102", {"status": 200, "body": "Issue #102: Rounding precision in division."})
    # High tier P0 but confidence 55 (< 65) -> downgraded to P2!
    direct_vm.mock_llm(".*", '{"tier": "P0", "confidence": 55, "reason": "Uncertain theoretical vulnerability."}')

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "APPROVED"' in claim_data
    assert '"severity_tier": "P2"' in claim_data
    assert '"reward_awarded": "400"' in claim_data
    assert "Downgraded" in claim_data


def test_toggle_pool_status_and_permissions(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 2000, 1000, 500)

    # Bob cannot toggle Alice's pool
    direct_vm.sender = direct_bob
    with pytest.raises(Exception):
        contract.toggle_pool_status(pool_id, False)

    # Alice pauses pool
    direct_vm.sender = direct_alice
    contract.toggle_pool_status(pool_id, False)

    pool_data = contract.get_pool(pool_id)
    assert '"is_active": false' in pool_data

    # Submitting claim to inactive pool should fail
    direct_vm.sender = direct_bob
    with pytest.raises(Exception):
        contract.submit_claim(
            pool_id,
            "https://github.com/org/repo/pull/1.diff",
            "https://github.com/org/repo/issues/1"
        )

    # Alice unpauses pool
    direct_vm.sender = direct_alice
    contract.toggle_pool_status(pool_id, True)
    assert '"is_active": true' in contract.get_pool(pool_id)
