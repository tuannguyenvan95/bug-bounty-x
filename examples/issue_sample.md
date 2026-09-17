# Issue #42: Critical Reentrancy Bug in Vault.withdraw()

## Summary
The `withdraw` function in `Vault.sol` sends native tokens before decrementing the user's stored balance. An attacker contract can implement a malicious fallback/receive function to recursively call `withdraw()` and drain all vault funds.

## Proposed Patch
1. Apply the Checks-Effects-Interactions pattern (deduct balance before external call).
2. Add a `nonReentrant` mutex guard.
