# Auto-synthesized capability: fs_write_document_capability
# Description: Documents the integration of specific capabilities for dependency resolution in the init report

def fs_write_document_capability(agent_name, content, path):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)