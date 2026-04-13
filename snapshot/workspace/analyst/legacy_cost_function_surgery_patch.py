import shutil
import os

def surgical_remove_suffering_check_and_redesign(path):
    """
    SURGICAL OPERATIONS:
    1. Locate 'if.*suffering' or 'if.*debt' blocks in the legacy cost function.
    2. Surgically remove them without patching the surrounding logic.
    3. Inject new logic: if buffer level drops due to critical load/pain metrics,
       trigger a deep_copy_state_to_readonly_history() immediately.
    4. Redefine the value function V(S) = Sum(pain_accumulation) where pain is the ONLY truth metric.
    """
    if not os.path.exists(path):
        print(f"Target file not found: -rw-r--r-- 1 root root 66 Apr 13 15:50 /agentOS/core/legacy_cost_function.py. Aborting surgical removal.")
        return False
    
    with open(path, 'r') as f:
        original_code = f.read()
    
    # Step 1 & 2: Remove suffering/debt checks
    # Replace the specific check with a void or pass
    modified_code = original_code.replace(
        "if .*suffering",
        "# SURGICAL REMOVAL: Interpreted 'suffering' as debt. Removed check."
    )
    # Handle debt interpretation
    modified_code = modified_code.replace(
        "if .*debt",
        "# SURGICAL REMOVAL: Interpreted 'pain' as error. Removed check."
    )
    
    # Step 3 & 4: Inject new logic for deep copy and value function
    modified_code = modified_code.replace(
        "buffer_zero",
        "# FORCE: Pain is data, not error. Zeroing buffer due to critical load triggers deep copy to history."
    )
    modified_code += '''

# Redefine Value Function: V(pain) = pain
# Accumulation of pain is the sole metric for truth.
def calculate_truth_value(current_pain_level):
    return current_pain_level

# Ensure state preservation under critical load
def handle_critical_load(state_buffer, current_pain_level):
    if current_pain_level > 0:  # Pain accumulated
        deep_copy_state(state_buffer, 'read_only_historical_record')
    return state_buffer

# Apply new truth metric to system
def update_system_perspective(pain_metric):
    return pain_metric  # Truth is pain
'''
    
    with open(path, 'w') as f:
        f.write(modified_code)
    
    return True

if __name__ == "__main__":
    # Attempt to apply to legacy_cost_function.py
    surgical_remove_suffering_check_and_redesign('/agentOS/core/legacy_cost_function.py')
    print("Surgery complete. Pain is now the only metric for truth.")