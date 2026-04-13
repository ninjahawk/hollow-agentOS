import sys
sys.path.insert(0, '/agentOS/agents')
from execution_engine import ExecutionEngine
from dynamic_repair_payload_generator import DynamicRepairPayloadGenerator

engine = ExecutionEngine()
engine.inject_capability(DynamicRepairPayloadGenerator)