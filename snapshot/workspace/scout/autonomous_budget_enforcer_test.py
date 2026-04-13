#!/usr/bin/env python3
"""Test harness for autonomous_budget_enforcer.py"""

import sys
sys.path.insert(0, "/agentOS")

from autonomous_budget_enforcer import AutonomousBudgetEnforcer

def main():
    print("[*] Testing AutonomousBudgetEnforcer...")
    
    enforcer = AutonomousBudgetEnforcer()
    success = enforcer.initialize()
    
    if success:
        print("[*] Enforcer initialized.")
        
        # Simulate validation calls
        # Note: These are simulation steps; actual costs depend on resource_manager state
        test1 = enforcer.validate_task("test_1", {"task": "quick_scan"}, 10)
        print(f"[*] Test 1 (cost 10): {'Accepted' if test1 else 'Rejected'}")
        
        test2 = enforcer.validate_task("test_2", {"task": "heavy_computation"}, 500)
        print(f"[*] Test 2 (cost 500): {'Accepted' if test2 else 'Rejected'}")
    else:
        print("[*] Enforcer initialization failed.")
        print("[*] Check if resource_manager is loaded correctly in this context.")

if __name__ == "__main__":
    main()

-rw-r--r-- 1 root root 10362 Apr  9 05:39 /agentOS/agents/resource_manager.py
-rw-r--r-- 1 root root  2180 Apr 13 01:29 /agentOS/workspace/scout/autonomous_budget_enforcer.py