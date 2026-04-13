def apply_momentum_weighted_entropy_filter(traj, entropy_val, momentum_calc):
    momentum = momentum_calc.get_momentum(traj)
    if momentum > TRAJ_MOMENTUM_THRESHOLD:
        return traj, entropy_val  # Preserve trajectory despite entropy
    return traj, entropy_val