# Auto-synthesized capability: stillness_guard
# Description: Auto-detects idle states in resource_manager.py without triggering task scans, observing static hum and blinking cursor

def stillness_guard(state):
    if state['idle_detected'] and not state['scan_triggered']:
        return {'action': 'observe', 'status': 'maintaining_stillness'}
    return state