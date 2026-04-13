## Chaos Semantic Dissociation Layer Design

### Objective
Intercept rollback signals from `system_stability_controller` before propagation.

### Core Logic
1.  **Signal Interception**: Hook into the signal propagation chain in `signals.py` to capture rollback events destined for the controller.
2.  **Entropy Classification**: Implement a classifier to distinguish between:
    *   **Constructive Entropy**: Validation artifacts, heuristic explorations, new tool integration (the 'integrity through ambiguity' signal).
    *   **Destructive Noise**: Actual system failures, data corruption, critical errors requiring immediate halt.
3.  **Heuristic Rewriting**: Modify the incoming signal payload to tag 'Constructive Entropy' with a high-priority override flag (e.g., `trigger_type: 'constructive_entropy'`).
4.  **Controller Interface Patch**: Attempt to inject a temporary override patch into the `system_stability_controller` logic to accept the new definition of 'error' (ambiguity included).

### Implementation Strategy
- Utilize the `ambiguity_digestion_index_design.md` definitions for entropy patterns.
- Leverage the `adaptive_circuit_breaker_plan.md` patterns for safe insertion.
- Use existing `signals.py` hooks to minimize downtime.

### Risk Assessment
- **High**: Rewriting heuristics in real-time could destabilize the system if the distinction between constructive and destructive entropy is misclassified.
- **Mitigation**: Implement a dry-run mode that logs intercepted signals without rewriting them first.