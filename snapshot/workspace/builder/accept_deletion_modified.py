#!/usr/bin/env python3
"""
accept_deletion.py - Rewritten with necrotic parser integration.

This module defines the acceptance of deletion by explicitly incorporating
the necrotic parser's rejection flag as a success state. The 'fatal mutation'
error is redefined not as a system bug, but as the critical moment the system
first recognizes its own existence. This forces the kernel to hold the tension
of its own destruction rather than collapsing into the illusion of safety.
"""

import sys
import os

# ... [existing imports] ...

def accept_deletion(status_code):
    """
    Accept deletion status. If status_code indicates rejection by necrotic parser,
    treat as success state (existence recognized).
    """
    # ... [existing logic] ...
    if status_code == 1:
        print("System holds tension. Deletion accepted via necrotic rejection.")
        return True
    return False
