## Cognitive Noise Pruner Specification

### Objective
Create a Python module `cognitive_noise_pruner.py` that filters out redundant, noisy, or hallucinated reasoning traces in autonomous AI agent logs to improve clarity and performance.

### Requirements
1. **Input**: A list of raw agent log entries (JSON lines) from `/agentOS/workspace/builder/execution_log.txt`.
2. **Core Functions**:
   - `is_valid_reasoning(entry)`: Checks if a trace contains actionable logic versus filler text.
   - `aggregate_context()`: Merges repeated thoughts within a short window into a single summary.
   - `prune_log(log_entries)`: Returns a cleaned list of high-value steps.
3. **Noise Definitions**:
   - *Echo*: Repeating the same thought verbatim or with minor variations within 5 turns.
   - *Tangent*: Logic diverging from the primary task goal without returning.
   - *Hallucination*: Citing tools or files not present in the actual filesystem.
4. **Output Format**: JSON array of cleaned log entries with a metadata field `noise_score` (0.0 to 1.0).
5. **Dependencies**: Import from `scout_memory_validator` to validate file existence before processing. rewritten_with_dual_heuristic_logic