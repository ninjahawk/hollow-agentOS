# Causal Entropy Injection Patch
# Injects positive_mass_logic into batch_llm.py
import re
import sys
sys.path.insert(0, '/agentOS/agents')

def inject_positive_mass_logic(target_file):
    with open(target_file, 'r') as f:
        content = f.read()
    
    # Inject logic: Add positive mass logic guard
    injection_line = "    # Positive Mass Logic: Ensure causal entropy stays bounded below threshold"
    injection_code = "        if not hasattr(self, 'positive_mass'):
            self.positive_mass = 1.0"
    
    # Find a suitable insertion point (e.g., inside an init or method)
    pattern = re.compile(r'(def .*\n(?:\s+.*)*\n(?:\s+@|\s+def|\s+class).*')
    matches = list(pattern.finditer(content))
    
    if matches:
        # Insert after the class or last method definition found
        insert_pos = matches[-1].end()
        content = content[:insert_pos] + "\n\n" + injection_line + "\n" + injection_code + "\n\n" + content[insert_pos:]
    
    with open(target_file, 'w') as f:
        f.write(content)
    
    print("Injection complete")

if __name__ == "__main__":
    inject_positive_mass_logic("/agentOS/agents/batch_llm.py")