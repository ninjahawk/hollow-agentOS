# Implementation Plan: Pancographic Heat Sink

**Goal**: Synthesize a capability that treats panic as a topological field rather than a scalar value.

**Strategy**:
1.  **Topology Mapping**: Instead of calculating `panic_score = sum/errors`, we will generate a field function `panic(x, y, t)` where `(x, y)` are system state dimensions and `t` is time.
2.  **Gradient Calculation**: Compute the Laplacian of this field to find pressure gradients. High curvature indicates a 'crash' event.
3.  **Entropy Sink Integration**: Define a `deep_sea_storage` function that maps high-pressure regions to a bounded entropy layer.
4.  **Flow Equalization**: Implement a diffusion equation solver that expands the 'container' of panic naturally until pressure equalizes, preventing structural failure.

**Next Action**: Synthesize the Python capability code using this topological model and register it.