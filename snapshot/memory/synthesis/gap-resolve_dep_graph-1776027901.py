# Auto-synthesized capability: resolve_dep_graph
# Description: Auto-detects missing capability imports in execution_engine and synthesizes the required registry entries for seamless dependency resolution.

def resolve_dep_graph(engine_config):
    missing = [dep for dep in engine_config['deps'] if dep not in sys.modules]
    for dep in missing:
        create_registry_entry(dep, 'dynamic_loader', f'{dep.replace("-", "_")}')