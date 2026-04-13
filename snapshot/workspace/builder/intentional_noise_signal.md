# Intentional Noise Signal Module

This module provides utilities for generating synthetic signals contaminated with specific types of noise, useful for stress-testing anomaly detection algorithms.

## Features
- **Signal Generation**: Creates sinusoidal base signals.
- **Noise Injection**: Supports Gaussian, Uniform, and Impulse noise profiles.
- **Outlier Detection**: Implements Z-score based filtering to clean data.

## Usage
```python
from intentional_noise_signal import generate_intentional_noise_signal

data = generate_intentional_noise_signal(length=100, signal_strength=1.0, noise_type='impulse', seed=123)
```