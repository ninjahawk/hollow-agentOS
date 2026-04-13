# Auto-synthesized capability: fs_write_documentation
# Description: Writes markdown documentation to resolve capability integration paths in the builder workspace

def fs_write_documentation(path, content, target_path):
    import os
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, 'w') as f:
        f.write(content)