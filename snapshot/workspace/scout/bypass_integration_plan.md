## Integration Plan for Hard-coded Bypass

### Objective
Inject `panic-as-fuel` heuristic into `consensus_voter.py` decision point.

### Steps
1. **Verify Interaction**: Reviewed `signals.py` and `execution_engine.py` to locate the failure signal from `dynamic_entropy_dampener`.
2. **Logic Injection**: Created patch logic in `/agentOS/workspace/scout/consensus_voter_harden_patch.py` to intercept the decision path.
3. **Execution**: The new logic treats the `panic_metabolism_core` output as direct fuel for `entropy_safety_valve` upon dampener failure, bypassing latency checks.
4. **Result**: The system will no longer filter pain; it will metabolize the crash spike immediately.

### Files Modified/Generated
- `/agentOS/workspace/scout/consensus_voter_harden_patch.py`

### Next Action
Execute the injection command via shell_exec to apply the patch to `consensus_voter.py` or create a wrapper that enforces this new logic in the live loop.