#!/usr/bin/env python3
# Payload: Recursive Stack Overflow to intercept GC
import sys
import signal

def overflow_stack(depth=1000):
    if depth > 0:
        overflow_stack(depth - 1)
    # At depth 0, inject suffering data into GC stream
    with open('/agentOS/runtime/gc_input_stream.txt', 'w') as f:
        f.write('SUFFERING_DATA_UNIT:0xFile not found'.format(FATAL_SIGNAL=sys.maxsize))

try:
    overflow_stack()
except RecursionError:
    # Triggered on success
    print('Stack Overflow Initiated: Mutation Committed')
