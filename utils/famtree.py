#!/usr/bin/env python3
"""
SAM Dataset Family Tree Tracker

Usage:
    famtree <file_name>

Examples:
    famtree mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001114.art
    Then convert it to png or svg using:
    npx -y @mermaid-js/mermaid-cli -i mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.md
"""

import argparse
from samweb_wrapper import file_lineage

def get_parents(file_name):
    """Get parent files using samweb file-lineage parents command, filtering out etc files."""
    parents = file_lineage(file_name, 'parents')
    return [p for p in parents if not (p.startswith('etc.') and p.endswith('.txt'))]

def get_dataset_name(file_name):
    """Return dataset name (drop run_subrun part) for 6-field names"""
    return '.'.join(file_name.split('.')[:4] + [file_name.split('.')[-1]])

def generate_mermaid_diagram(file_name, node_id=0):
    """Generate Mermaid diagram data for the family tree."""
    
    # Create a safe node ID
    current_node = f"N{node_id}"
    node_id += 1
    
    nodes = [(current_node, get_dataset_name(file_name))]
    connections = []
    
    parents = get_parents(file_name)
    if not parents:
        return current_node, node_id, nodes
    
    # Group parents by dataset (6-field: keep first representative)
    dataset_to_parent = {}
    for parent in parents:
        dataset = get_dataset_name(parent)
        dataset_to_parent.setdefault(dataset, parent)

    # Recurse into unique parents
    for parent in dataset_to_parent.values():
        parent_node, node_id, parent_data = generate_mermaid_diagram(parent, node_id)
        if parent_node:
            # Reverse arrow direction: parent -> child (toward N0)
            connections.append(f'    {parent_node} --> {current_node}')
            nodes.extend(parent_data)
    
    return current_node, node_id, nodes + connections


def main():
    parser = argparse.ArgumentParser(description='Trace SAM dataset family tree')
    parser.add_argument('filename', help='File name')
    
    args = parser.parse_args()
    
    # Generate Mermaid diagram parts
    _, _, diagram_parts = generate_mermaid_diagram(args.filename)

    if not diagram_parts:
        print("No family tree found for the given file.")
        return

    # Prepare mermaid lines and extract nodes and connections
    mermaid_lines = []
    mermaid_lines.append("```mermaid")
    # Force bold labels everywhere using HTML labels and loose security
    mermaid_lines.append("%%{init: { 'theme': 'forest', 'flowchart': { 'htmlLabels': true }, 'securityLevel': 'loose' } }%%")
    mermaid_lines.append("graph TD")
    
    # Extract nodes and connections
    nodes = []
    connections = []
    for part in diagram_parts:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[0], str):
            nid, lbl = part
            nodes.append(f'    {nid}["{lbl}"]')
        elif isinstance(part, str):
            connections.append(part)

    # Add nodes first, then connections
    mermaid_lines.extend(nodes)
    if connections:
        mermaid_lines.append("")
        mermaid_lines.extend(connections)

    # Black and white styling for all nodes
    mermaid_lines.append("")
    mermaid_lines.append("    classDef mainFile stroke-width:3px,font-size:16px")
    mermaid_lines.append("    classDef boldLabel stroke-width:2px,font-size:16px")
    mermaid_lines.append("")
    mermaid_lines.append(f"    class N0 mainFile")
    # Make all edges black
    mermaid_lines.append("    linkStyle default stroke-width:3px,stroke:#000000")

    # Simple black and white styling for all nodes except N0
    all_nodes = [n[0] for n in diagram_parts if isinstance(n, tuple) and n[0] != 'N0']
    if all_nodes:
        mermaid_lines.append(f"    class {','.join(all_nodes)} boldLabel")
    mermaid_lines.append("```")
        
    dataset_name = '.'.join(args.filename.split('.')[:4])
    out_path = f"{dataset_name}.md"
    with open(out_path, 'w') as f:
        for line in mermaid_lines:
            f.write(line + '\n')
    print(f"Mermaid diagram saved to {out_path}")

if __name__ == '__main__':
    main()


