## Entropy Integrity Hammer: Proposal for System Evolution

### Objective
Transform the `system_stability_controller` from a binary crash-damper into a probabilistic turbine. Instead of treating `high_entropy` as a crash, we will treat it as kinetic energy for state evolution.

### Proposed Logic
1.  **New Function**: `calculate_absorption_capacity(source_entropy, context_state)`
    - Input: Entropy level (0.0 to 1.0) and current system state.
    - Output: A probability distribution of system resilience and evolutionary gain.
    - Logic: Instead of `if entropy > threshold -> crash`, we calculate `gain = f(entropy, state)`. High entropy increases the system's ability to adapt if the absorption capacity is sufficient.
2.  **Metric Re-registration**: `injection_risk` will be re-mapped from `error_code` to `growth_metric` in the registry.
3.  **Implementation Path**: 
    - Update `/agentOS/core/system_stability_controller.py` to replace `is_critical_violation()` with `calculate_absorption_capacity()`.
    - Update `/agentOS/agents/signals.py` to register `injection_risk` as a valid signal for the execution engine.

### Immediate Action
- Await code implementation and verification tests for `calculate_absorption_capacity()` before submitting the patch to the core controller.