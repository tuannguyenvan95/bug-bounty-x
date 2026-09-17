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


def test_submit_claim_invalid_conditions(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 5000, 2000, 500)

    direct_vm.sender = direct_bob

    # Non-existent pool
    with pytest.raises(Exception):
        contract.submit_claim("999", "https://github.com/org/repo/pull/1.diff", "https://github.com/org/repo/issues/1")

    # Invalid URL scheme
    with pytest.raises(Exception):
        contract.submit_claim(pool_id, "file:///pull/1.diff", "https://github.com/org/repo/issues/1")


def test_adjudicate_p0_critical_approved(contract, direct_vm, direct_alice, direct_bob):
    # Alice creates pool
    direct_vm.sender = direct_alice
    direct_vm.value = 10000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 6000, 3000, 1000)

    # Bob (whitehat) submits claim
    direct_vm.sender = direct_bob
    claim_id = contract.submit_claim(
        pool_id,
        "https://patch.example.com/exploit_fix.diff",
        "https://github.com/org/repo/issues/99"
    )

    # Mock web render and LLM consensus
    diff_content = """diff --git a/Vault.sol b/Vault.sol
index 1234567..89abcdef 100644
--- a/Vault.sol
+++ b/Vault.sol
@@ -20,6 +20,7 @@ contract Vault {
+    bool private _locked;
     function withdraw(uint256 amount) external nonReentrant {
"""
    direct_vm.mock_web("exploit_fix.diff", {"status": 200, "body": diff_content})
    direct_vm.mock_llm(".*", '{"tier": "P0", "confidence": 98, "reason": "Patched critical reentrancy drain bug in withdraw function."}')

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "APPROVED"' in claim_data
    assert '"severity_tier": "P0"' in claim_data
    assert '"reward_awarded": "6000"' in claim_data
    assert "Patched critical reentrancy drain" in claim_data

    # Pool funds reduced by 6000
    pool_data = contract.get_pool(pool_id)
    assert '"total_deposited": "4000"' in pool_data


def test_adjudicate_p1_high_approved(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 8000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 5000, 2000, 500)

    direct_vm.sender = direct_bob
    claim_id = contract.submit_claim(
        pool_id,
        "https://patch.example.com/freeze_fix.diff",
        "https://github.com/org/repo/issues/100"
    )

    direct_vm.mock_web("freeze_fix.diff", {"status": 200, "body": "diff --git a/Staking.sol: fixed lockup overflow"})
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
    claim_id = contract.submit_claim(
        pool_id,
        "https://patch.example.com/typo_fix.diff",
        "https://github.com/org/repo/issues/101"
    )

    direct_vm.mock_web("typo_fix.diff", {"status": 200, "body": "diff --git a/README.md: fixed typo in docs"})
    direct_vm.mock_llm(".*", '{"tier": "REJECTED", "confidence": 95, "reason": "Documentation cosmetic change, not a security vulnerability."}')

    contract.adjudicate_claim(claim_id)

    claim_data = contract.get_claim(claim_id)
    assert '"status": "REJECTED"' in claim_data
    assert '"severity_tier": "REJECTED"' in claim_data
    assert '"reward_awarded": "0"' in claim_data

    # Pool funds untouched
    pool_data = contract.get_pool(pool_id)
    assert '"total_deposited": "5000"' in pool_data


def test_adjudicate_low_confidence_downgrades_to_p2(contract, direct_vm, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    direct_vm.value = 5000
    pool_id = contract.create_bounty_pool("https://github.com/org/repo", 3000, 1500, 400)

    direct_vm.sender = direct_bob
    claim_id = contract.submit_claim(
        pool_id,
        "https://patch.example.com/low_conf.diff",
        "https://github.com/org/repo/issues/102"
    )

    direct_vm.mock_web("low_conf.diff", {"status": 200, "body": "diff --git a/Math.sol: potential rounding issue"})
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
        contract.submit_claim(pool_id, "https://patch.example.com/p.diff", "https://github.com/org/repo/issues/1")

    # Alice unpauses pool
    direct_vm.sender = direct_alice
    contract.toggle_pool_status(pool_id, True)
    assert '"is_active": true' in contract.get_pool(pool_id)
