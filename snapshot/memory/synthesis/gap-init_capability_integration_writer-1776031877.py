# Auto-synthesized capability: init_capability_integration_writer
# Description: Writes capability integration progress reports and dependency resolution logs to the filesystem for offline review and audit.

def write_capability_report(path, content):
    with open(path, 'a') as f:
        f.write(content + '\n\n')