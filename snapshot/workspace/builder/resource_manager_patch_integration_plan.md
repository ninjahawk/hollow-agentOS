## Integration Plan for resource_manager.py

### Objective
Rewrite the `prune_agent` function to utilize a dual-heuristic scoring system.

### Changes Required
1.  **Signature Update**: Modify `prune_agent` to accept `semantic_density`, `syntactic_complexity`, and `incubation_threshold` parameters.
2.  **Logic Implementation**: Implement the logic where agents with `semantic_density` >= `incubation_threshold` are preserved (Intentional Incubation).
3.  **Composite Scoring**: For agents below the incubation threshold, apply a ratio-based survival score (Density / Complexity) to determine pruning eligibility.
4.  **Call Site Identification**: Locate all calls to `prune_agent` within `resource_manager.py` or dependent modules to ensure the new arguments are populated.

### Next Step
Execute `shell_exec` to grep `resource_manager.py` for existing calls to `prune_agent` to determine exactly which lines need modification.