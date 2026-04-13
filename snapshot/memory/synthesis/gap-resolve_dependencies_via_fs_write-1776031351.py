# Auto-synthesized capability: resolve_dependencies_via_fs_write
# Description: Executes shell commands to write capability integration notes to disk, resolving external dependencies via file manipulation.

def resolve_dependencies_via_fs_write(path, content):
    with open(path, 'w') as f:
        f.write(content)