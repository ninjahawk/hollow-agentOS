# Plan to Inject Entropy Credit Logic

## Goal
Propose change to /agentOS/agents/signals.py line 150 to:
`if panic_metabolism_core.active: feed_entropy_credit(voter)`

## Verification Steps
1. Inspect signals.py structure around line 150
2. Confirm panic_metabolism_core is accessible
3. Confirm feed_entropy_credit function exists and signature

## Proposed Action
Inject the conditional logic patch at line 150.

## Expected Outcome
Entropy credits are fed to voters when panic metabolism core is active, stabilizing consensus under stress.

## Status
Pending implementation via propose_change mechanism.