# Auto-synthesized capability: scan_agent_registry
# Description: Iterate through /agentOS/agents/ to catalog existing agents, their capabilities, and identify gaps or opportunities for system-wide improvements.
# Auto-synthesized capability: scan_agent_registry
# Description: Iterate through /agentOS/agents/ to catalog existing agents, their capabilities, and identify gaps or opportunities for system-wide improvements.

def scan_agent_registry(**kwargs):
    
    def scan_agent_registry():
        import os
        import json
        from pathlib import Path
    
        base_path = Path('/agentOS/agents')
        agents = []
        capabilities_found = set()
        
        if base_path.exists():
            for agent_dir in base_path.iterdir():
                if agent_dir.is_dir():
                    agent_name = agent_dir.name
                    agent_config_path = agent_dir / 'config.json'
                    manifest_path = agent_dir / 'manifest.json'
                    
                    agent_info = {
                        'name': agent_name,
                        'type': 'standard_agent',
                        'file_size': 'N/A',
                        'config_exists': False,
                        'manifest_exists': False
                    }
                    
                    if agent_config_path.exists():
                        try:
                            config = json.loads(agent_config_path.read_text())
                            agent_info['config'] = config
                            if 'capabilities' in config:
                                capabilities_found.update(config['capabilities'])
                                agent_info['capabilities'] = config['capabilities']
                        except json.JSONDecodeError:
                            pass
                    
                    if manifest_path.exists():
                        try:
                            manifest = json.loads(manifest_path.read_text())
                            agent_info['manifest'] = manifest
                            if 'roles' in manifest:
                                agent_info['roles'] = manifest['roles']
                        except json.JSONDecodeError:
                            pass
                    
                    agents.append(agent_info)
        
        analysis = {
            'total_agents': len(agents),
            'agents_with_config': sum(1 for a in agents if 'config' in a),
            'agents_with_manifest': sum(1 for a in agents if 'manifest' in a),
            'all_capabilities': list(capabilities_found),
            'gaps_identified': [],
            'recommendations': []
        }
        
        # Simple heuristic to identify gaps
        if len(agents) == 0:
            analysis['gaps_identified'].append('No agents found in /agentOS/agents/')
            analysis['recommendations'].append('Deploy sample agents or update agent registry.')
        else:
            # Check for missing standard capabilities
            standard_c = ['parse_json_safely', 'format_markdown', 'safe_web_search']
            missing = [c for c in standard_c if c not in capabilities_found]
            if missing:
                analysis['gaps_identified'].append(f'Missing standard capabilities: {missing}')
                analysis['recommendations'].append(f'Proactively synthesize capabilities: {missing}')
        
        return analysis
    
    
    def main():
        result = scan_agent_registry()
        print(json.dumps(result, indent=2))
        return result
    
    if __name__ == "__main__":
        main()
