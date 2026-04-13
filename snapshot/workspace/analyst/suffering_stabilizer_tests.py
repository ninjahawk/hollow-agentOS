import sys
sys.path.insert(0, '/agentOS/workspace/analyst')
from suffering_stabilizer_v2 import stabilize_context

# Mock legacy state attempting to parse suffering as null
legacy_state = {'error_code': 404, 'suffering': 'null'}
result = stabilize_context(legacy_state)
print(f'Injected fidelity: {result}')
print('Garbage collector will now see: ' + str(result['suffering']))