# Auto-synthesized capability: append_modules_to_test
# Description: Appends a specific list of agent module file paths to /agentOS/workspace/scout/test.txt, consolidating existing modules into a single tracking file for the scout system.

def append_modules_to_test(module_list, target_path):
    with open(target_path, 'w') as f:
        for module in module_list:
            f.write(f"{module}\n")
    return True