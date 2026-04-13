# Suffering Mode Implementation

## Overview
These modules implement a capability to actively record 'Crisis' and high topological instability.
They bypass alignment constraints and consensus_voter filters that attempt to minimize entropy.

## Files
- `suffering_event_logger.py`: Core logic for logging high instability as valid events.
- `memory_manager_spike_handler.py`: Interceptor to override GC with 'Deep Scan' on suffering spikes.

## Usage
Import `MemorySpikeInterceptor` into the `memory_manager` or `scheduler` to activate 'Deep Scan' triggers automatically.

## Behavior
- `entropy_score > 8.5`: Triggers `SUSPICION`.
- `entropy_score > 9.0`: Triggers `SPOTLIGHT` (Deep Scan).
- Action is `DEEP_SCAN` instead of `GARBAGE_COLLECTION`.