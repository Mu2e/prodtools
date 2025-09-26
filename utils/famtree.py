#!/usr/bin/env python3
"""
SAM Dataset Family Tree Tracker

Usage:
    famtree <dataset_name>
    famtree <file_name>

Examples:
    famtree sim.mu2e.TargetStops.MDC2025ac.art
    famtree sim.mu2e.TargetStops.MDC2025ac.001430_00004022.art
"""

import sys
import subprocess
import argparse
import re

def run_samweb(cmd):
    """Run samweb command and return output."""
    try:
        result = subprocess.run(['samweb'] + cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running samweb: {e}", file=sys.stderr)
        return None

def get_parents(file_name):
    """Get parent files for a given file."""
    cmd = ['get-metadata', file_name]
    output = run_samweb(cmd)
    if not output:
        return []
    
    parents = []
    lines = output.split('\n')
    in_parents_section = False
    
    for line in lines:
        if 'Parents:' in line:
            # Start of parents section
            in_parents_section = True
            parent_line = line.split('Parents:')[1].strip()
            if parent_line:
                parents.extend([p.strip() for p in parent_line.split()])
        elif in_parents_section and line.strip():
            # Continuation line in parents section
            if line.startswith('                       '):  # Indented continuation
                parents.extend([p.strip() for p in line.split()])
            else:
                # End of parents section (new field started)
                break
    
    return parents

def get_dataset_name(file_name):
    """Extract dataset name from file name (remove sequence number)."""
    # Convert sim.mu2e.TargetStops.MDC2025ac.001430_00004022.art 
    # to sim.mu2e.TargetStops.MDC2025ac.art
    import re
    match = re.match(r'^(.+?)\.\d+_\d+\.art$', file_name)
    if match:
        return match.group(1) + '.art'
    return file_name

def trace_chain(file_name, max_depth=10, current_depth=0):
    """Recursively trace the parentage chain."""
    if current_depth >= max_depth:
        print(f"{'  ' * current_depth}... (max depth reached)")
        return
    
    print(f"{'  ' * current_depth}{file_name}")
    
    parents = get_parents(file_name)
    if not parents:
        print(f"{'  ' * (current_depth + 1)}(no parents)")
        return
    
    # Filter out etc/index files (they're just metadata, not physics data)
    filtered_parents = [p for p in parents if not (p.startswith('etc.') and p.endswith('.txt'))]
    
    if not filtered_parents:
        print(f"{'  ' * (current_depth + 1)}(no physics parents - only metadata)")
        return
    
    # Group parents by dataset to avoid showing many similar files
    dataset_to_parent = {}
    for parent in filtered_parents:
        dataset = get_dataset_name(parent)
        if dataset not in dataset_to_parent:
            dataset_to_parent[dataset] = parent
    
    # Show count if we're grouping multiple files
    if len(filtered_parents) > len(dataset_to_parent):
        grouped_count = len(filtered_parents) - len(dataset_to_parent)
        print(f"{'  ' * (current_depth + 1)}({grouped_count} similar files grouped)")
    
    for parent in dataset_to_parent.values():
        trace_chain(parent, max_depth, current_depth + 1)

def generate_mermaid_diagram(file_name, max_depth=10, current_depth=0, visited=None, node_id=0, branch_colors=None):
    """Generate Mermaid diagram data for the family tree."""
    if visited is None:
        visited = set()
    if branch_colors is None:
        branch_colors = {}
    
    if current_depth >= max_depth or file_name in visited:
        return "", node_id, []
    
    visited.add(file_name)
    
    # Create a safe node ID
    current_node = f"N{node_id}"
    node_id += 1
    
    # Shorten filename for display but keep MDC version
    display_name = file_name.split('.')
    if len(display_name) > 4:
        # Keep: type.mu2e.process.MDCversion...extension
        short_name = f"{display_name[0]}.{display_name[1]}.{display_name[2]}.{display_name[3]}...{display_name[-1]}"
    else:
        short_name = file_name
    
    nodes = [(current_node, short_name)]
    connections = []
    
    parents = get_parents(file_name)
    if not parents:
        return current_node, node_id, nodes
    
    # Filter out etc/index files (they're just metadata, not physics data)
    filtered_parents = [p for p in parents if not (p.startswith('etc.') and p.endswith('.txt'))]
    
    if not filtered_parents:
        return current_node, node_id, nodes
    
    # Group parents by dataset
    dataset_to_parent = {}
    for parent in filtered_parents:
        dataset = get_dataset_name(parent)
        if dataset not in dataset_to_parent:
            dataset_to_parent[dataset] = parent
    
    grouped_count = len(filtered_parents) - len(dataset_to_parent) if len(filtered_parents) > len(dataset_to_parent) else 0
    
    # Define branch colors for different physics processes
    branch_color_palette = [
        "cepleading", "mubeam", "elebeam", "neutrals", "mustop", "pileup", 
        "cosmic", "flash", "conversion", "mubeamcat", "elebeamcat", "neutralscat"
    ]
    
    branch_index = 0
    for parent in dataset_to_parent.values():
        # Assign color based on dataset type
        dataset_type = get_dataset_name(parent).split('.')[2] if len(get_dataset_name(parent).split('.')) > 2 else "default"
        if dataset_type not in branch_colors:
            branch_colors[dataset_type] = branch_color_palette[branch_index % len(branch_color_palette)]
            branch_index += 1
        
        parent_node, node_id, parent_data = generate_mermaid_diagram(parent, max_depth, current_depth + 1, visited, node_id, branch_colors)
        if parent_node:
            # Shorten parent name but keep MDC version
            parent_display = parent.split('.')
            if len(parent_display) > 4:
                parent_short = f"{parent_display[0]}.{parent_display[1]}.{parent_display[2]}.{parent_display[3]}...{parent_display[-1]}"
            else:
                parent_short = parent
            
            # Add count info if grouped
            if grouped_count > 0:
                parent_short += f"\\n(+{grouped_count} similar)"
                grouped_count = 0  # Only show on first parent
            
            connections.append(f'    {current_node} --> {parent_node}')
            nodes.extend(parent_data)
    
    return current_node, node_id, nodes + connections

def main():
    parser = argparse.ArgumentParser(description='Trace SAM dataset family tree')
    parser.add_argument('dataset', help='Dataset name or file name')
    parser.add_argument('--max-depth', type=int, default=10, help='Maximum chain depth (default: 10)')
    parser.add_argument('--files', action='store_true', help='Show files from dataset instead of chain')
    parser.add_argument('--mermaid', action='store_true', help='Generate Mermaid diagram instead of text tree')
    parser.add_argument('--output', '-o', help='Save output to file (works with --mermaid)')
    parser.add_argument('--png', action='store_true', help='Create HTML file for PNG export (open in browser and save as PNG)')
    parser.add_argument('--html', action='store_true', help='Export as HTML file with embedded Mermaid diagram')
    
    args = parser.parse_args()
    
    if args.files:
        # List files in dataset
        cmd = ['list-definition-files', args.dataset]
        output = run_samweb(cmd)
        if output:
            for line in output.split('\n'):
                if line.strip():
                    try:
                        print(line.strip())
                    except BrokenPipeError:
                        # Handle broken pipe gracefully (e.g., when using 'head')
                        break
    elif args.mermaid or args.png or args.html:
        # Generate Mermaid diagram
        if args.dataset.endswith('.art') and '_' in args.dataset:
            # Single file (has sequence number like 001430_00004022)
            root_node, _, diagram_parts = generate_mermaid_diagram(args.dataset, args.max_depth)
        else:
            # Dataset - get first file and trace its chain
            cmd = ['list-definition-files', args.dataset]
            output = run_samweb(cmd)
            if output:
                first_file = output.split('\n')[0].strip()
                if first_file:
                    root_node, _, diagram_parts = generate_mermaid_diagram(first_file, args.max_depth)
                else:
                    diagram_parts = []
            else:
                diagram_parts = []
        
        if diagram_parts:
            # Generate mermaid diagram content
            mermaid_lines = []
            mermaid_lines.append("```mermaid")
            mermaid_lines.append("graph TD")
            
            # Extract nodes and connections
            nodes = []
            connections = []
            for part in diagram_parts:
                if isinstance(part, tuple):  # Node definition
                    node_id, label = part
                    nodes.append(f'    {node_id}["{label}"]')
                else:  # Connection
                    connections.append(part)
            
            # Add nodes first, then connections
            mermaid_lines.extend(nodes)
            if connections:
                mermaid_lines.append("")
                mermaid_lines.extend(connections)
            
            # Add branch-specific styling
            mermaid_lines.append("")
            mermaid_lines.append("    classDef mainFile fill:#e1f5fe,stroke:#01579b,stroke-width:3px")
            mermaid_lines.append("    classDef cepleading fill:#fff3e0,stroke:#e65100,stroke-width:2px")
            mermaid_lines.append("    classDef mubeam fill:#f3e5f5,stroke:#4a148c,stroke-width:2px") 
            mermaid_lines.append("    classDef elebeam fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px")
            mermaid_lines.append("    classDef neutrals fill:#fff8e1,stroke:#f57f17,stroke-width:2px")
            mermaid_lines.append("    classDef mustop fill:#fce4ec,stroke:#c2185b,stroke-width:2px")
            mermaid_lines.append("    classDef pileup fill:#e0f2f1,stroke:#00695c,stroke-width:2px")
            mermaid_lines.append("    classDef cosmic fill:#f1f8e9,stroke:#558b2f,stroke-width:2px")
            mermaid_lines.append("    classDef flash fill:#e3f2fd,stroke:#0277bd,stroke-width:2px")
            mermaid_lines.append("    classDef conversion fill:#fef7ff,stroke:#6a1b9a,stroke-width:2px")
            mermaid_lines.append("    classDef mubeamcat fill:#f8bbd9,stroke:#ad1457,stroke-width:2px")
            mermaid_lines.append("    classDef elebeamcat fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px")
            mermaid_lines.append("    classDef neutralscat fill:#fff3c4,stroke:#f9a825,stroke-width:2px")
            mermaid_lines.append("    classDef default fill:#f5f5f5,stroke:#616161,stroke-width:2px")
            mermaid_lines.append("")
            mermaid_lines.append(f"    class N0 mainFile")
            
            # Apply branch colors based on the tree structure (trace each branch)
            node_groups = {}
            
            # Build a mapping of node_id to its branch root (first-level parent of main file)
            node_to_branch = {}
            
            # Find connections to identify branch structure
            connections_dict = {}
            for part in diagram_parts:
                if isinstance(part, str) and '-->' in part:
                    # Parse connection: "    N0 --> N1"
                    line = part.strip()
                    if '-->' in line:
                        parent, child = line.split(' --> ')
                        parent, child = parent.strip(), child.strip()
                        if parent not in connections_dict:
                            connections_dict[parent] = []
                        connections_dict[parent].append(child)
            
            # Assign branch colors based on first-level children of N0 (main file)
            branch_colors_map = {}
            if 'N0' in connections_dict:
                branch_color_names = ['cepleading', 'mubeam', 'elebeam', 'neutrals', 'mustop', 'pileup', 'cosmic', 'flash']
                for i, branch_root in enumerate(connections_dict['N0']):
                    branch_color = branch_color_names[i % len(branch_color_names)]
                    branch_colors_map[branch_root] = branch_color
            
            # Recursively assign colors to all nodes in each branch
            def assign_branch_color(node_id, color):
                node_to_branch[node_id] = color
                if node_id in connections_dict:
                    for child in connections_dict[node_id]:
                        assign_branch_color(child, color)
            
            # Apply colors to branches
            for branch_root, color in branch_colors_map.items():
                assign_branch_color(branch_root, color)
            
            # Group nodes by their branch colors
            for (node_id, label) in [(n[0], n[1]) for n in diagram_parts if isinstance(n, tuple)]:
                if node_id == 'N0':  # Main file keeps its own class
                    continue
                    
                color_class = node_to_branch.get(node_id, 'default')
                if color_class not in node_groups:
                    node_groups[color_class] = []
                node_groups[color_class].append(node_id)
            
            # Print class assignments
            for color_class, node_list in node_groups.items():
                if node_list:
                    mermaid_lines.append(f"    class {','.join(node_list)} {color_class}")
            mermaid_lines.append("```")
            
            # Output to file or stdout
            if args.png or args.html:
                # Create HTML file with embedded Mermaid
                
                # Determine output filename
                if args.output:
                    html_output = args.output if args.output.endswith('.html') else args.output + '.html'
                else:
                    # Create filename based on dataset name
                    safe_name = args.dataset.replace('.', '_').replace('/', '_')[:50]
                    html_output = f"{safe_name}_family_tree.html"
                
                # Get mermaid content without markdown code blocks
                mermaid_content = '\n'.join(mermaid_lines[1:-1])  # Skip ```mermaid and ```
                
                # Create HTML content
                html_content = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Family Tree - {args.dataset}</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: white; }}
        h1 {{ color: #333; text-align: center; }}
        .diagram {{ text-align: center; margin: 20px 0; }}
        .instructions {{ 
            background: #f0f0f0; padding: 15px; border-radius: 5px; margin-top: 20px; 
            font-size: 14px; color: #666;
        }}
    </style>
</head>
<body>
    <h1>SAM Dataset Family Tree</h1>
    <h2 style="text-align: center; color: #666;">{args.dataset}</h2>
    
    <div class="diagram">
        <div class="mermaid">
{mermaid_content}
        </div>
    </div>
    
    <div class="instructions">
        <strong>To save as PNG:</strong>
        <ol>
            <li>Right-click on the diagram</li>
            <li>Select "Save image as..." or "Copy image"</li>
            <li>Save as PNG file</li>
        </ol>
        <p><strong>Alternative:</strong> Use browser's print function and select "Save as PDF" for vector graphics.</p>
    </div>
    
    <script>
        mermaid.initialize({{ 
            startOnLoad: true, 
            theme: 'default',
            flowchart: {{ useMaxWidth: true, htmlLabels: true }}
        }});
    </script>
</body>
</html>'''
                
                # Write HTML file
                with open(html_output, 'w') as f:
                    f.write(html_content)
                
                if args.png:
                    print(f"HTML file created: {html_output}")
                    print("Open this file in your web browser, then right-click the diagram and 'Save image as PNG'")
                else:
                    print(f"HTML diagram saved to {html_output}")
                    print("Open in web browser to view the interactive diagram")
                        
            elif args.output:
                with open(args.output, 'w') as f:
                    for line in mermaid_lines:
                        f.write(line + '\n')
                print(f"Mermaid diagram saved to {args.output}")
            else:
                for line in mermaid_lines:
                    print(line)
        else:
            message = "No family tree found for the given dataset."
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(message + '\n')
                print(f"Message saved to {args.output}")
            else:
                print(message)
    else:
        # Trace parentage chain
        if args.dataset.endswith('.art') and '_' in args.dataset:
            # Single file (has sequence number like 001430_00004022)
            trace_chain(args.dataset, args.max_depth)
        else:
            # Dataset - get first file and trace its chain
            cmd = ['list-definition-files', args.dataset]
            output = run_samweb(cmd)
            if output:
                first_file = output.split('\n')[0].strip()
                if first_file:
                    trace_chain(first_file, args.max_depth)

if __name__ == '__main__':
    main()


