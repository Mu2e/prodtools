#!/usr/bin/env python3
"""
SAM Dataset Family Tree Tracker

Usage:
    famtree <file_name_or_dataset> [--stats] [--max-files N] [--png] [--svg]

Examples:
    # Individual file
    famtree mcs.mu2e.CeMLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001114.art
    
    # Dataset name (uses first file from dataset)
    famtree sim.mu2e.MuminusStopsCat.MDC2025ac.art
    
    # Generate with efficiency statistics
    famtree dig.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001999.art --stats
    
    # Generate PNG with statistics (sample 5 files per dataset for speed)
    famtree dig.mu2e.CePLeadingLogMix1BBTriggered.MDC2020ba_best_v1_3.001202_00001999.art --stats --max-files 5 --png
    
    # Generate both PNG and SVG
    famtree sim.mu2e.MuminusStopsCat.MDC2025ac.art --png --svg
    
    # Manual conversion (if options not used)
    npx -y @mermaid-js/mermaid-cli -i sim.mu2e.MuminusStopsCat.MDC2025ac.md
"""

import argparse
import os
import sys
from samweb_wrapper import file_lineage, list_definition_files, get_samweb_wrapper
from genFilterEff import process_dataset

def get_parents(file_name):
    """Get parent files using samweb file-lineage parents command, filtering out etc files."""
    parents = file_lineage(file_name, 'parents')
    return [p for p in parents if not (p.startswith('etc.') and p.endswith('.txt'))]

def get_dataset_name(file_name):
    """Return dataset name (drop run_subrun part) for 6-field names"""
    return '.'.join(file_name.split('.')[:4] + [file_name.split('.')[-1]])

def get_first_file_from_dataset(dataset_name):
    """Get the first file from a dataset name (without run/subrun)."""
    # Use list_definition_files since dataset names are definitions in SAM
    files = list_definition_files(dataset_name)
    if files:
        return files[0]
    else:
        print(f"No files found for dataset: {dataset_name}")
        return None

def get_dataset_efficiency(dataset_name, samweb, max_files=10, verbosity=0, extrapolate=True):
    """Get efficiency statistics for a dataset using process_dataset from genFilterEff.
    
    Args:
        dataset_name: Dataset name (type.mu2e.process.MDCversion.ext)
        samweb: SAMWeb wrapper instance
        max_files: Maximum number of files to sample (default: 10 for speed)
        verbosity: Verbosity level (0=quiet)
        extrapolate: If True, scale sampled stats to full dataset (default: True)
    
    Returns:
        Tuple of (passed_events, generated_events, efficiency, num_files, is_extrapolated) or None if unavailable
        If extrapolate=True, counts are scaled to full dataset size.
        is_extrapolated indicates if the stats were extrapolated from a sample.
    """
    try:
        # Get total number of files in dataset
        file_list = samweb.list_files(f"dh.dataset={dataset_name} with availability anylocation")
        num_files_total = len(file_list)
        
        # Use the same process_dataset function from genFilterEff
        summary = process_dataset(
            dataset_name,
            samweb,
            chunk_size=100,
            max_files=max_files,
            verbosity=verbosity
        )
        
        # If we sampled fewer files than total, extrapolate the counts
        if extrapolate and summary.nfiles > 0 and summary.nfiles < num_files_total:
            scale_factor = num_files_total / summary.nfiles
            extrapolated_passed = int(summary.passedevents * scale_factor)
            extrapolated_generated = int(summary.genevents * scale_factor)
            # Efficiency remains the same (ratio doesn't change)
            eff = summary.efficiency()
            return (extrapolated_passed, extrapolated_generated, eff, num_files_total, True)
        else:
            return (summary.passedevents, summary.genevents, summary.efficiency(), num_files_total, False)
        
    except Exception:
        # Dataset doesn't have gencount or other issue
        return None

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
    parser.add_argument('filename', help='File name or dataset name (without run/subrun)')
    parser.add_argument('--png', action='store_true', help='Convert Mermaid diagram to PNG using mmdc')
    parser.add_argument('--svg', action='store_true', help='Convert Mermaid diagram to SVG using mmdc')
    parser.add_argument('--stats', action='store_true', help='Include efficiency statistics in node labels')
    parser.add_argument('--max-files', type=int, default=10, help='Max files to sample for stats (default: 10)')
    
    args = parser.parse_args()
    
    # Check if input is a dataset name (no run/subrun) or individual file
    parts = args.filename.split('.')
    
    if len(parts) == 5:  # Dataset name: sim.mu2e.MuminusStopsCat.MDC2025ac.art
        print(f"Dataset name detected: {args.filename}")
        actual_file = get_first_file_from_dataset(args.filename)
        if not actual_file:
            return
        print(f"Using first file: {actual_file}")
        file_to_process = actual_file
    elif len(parts) == 6:  # Individual file: sim.mu2e.MuminusStopsCat.MDC2025ac.001430_00000000.art
        file_to_process = args.filename
    else:
        print(f"Invalid filename format. Expected 5 fields (dataset) or 6 fields (file), got {len(parts)} fields.")
        return
    
    # Generate Mermaid diagram parts
    _, _, diagram_parts = generate_mermaid_diagram(file_to_process)

    if not diagram_parts:
        print("No family tree found for the given file.")
        return

    # Initialize SAMWeb wrapper if stats are requested
    samweb = None
    if args.stats:
        samweb = get_samweb_wrapper()
        print("Fetching efficiency statistics...")

    # Prepare mermaid lines and extract nodes and connections
    mermaid_lines = []
    mermaid_lines.append("```mermaid")
    # Force bold labels everywhere using HTML labels and loose security
    # Set large wrappingWidth to prevent line wrapping
    mermaid_lines.append("%%{init: { 'theme': 'forest', 'flowchart': { 'htmlLabels': true, 'wrappingWidth': 9999 }, 'securityLevel': 'loose' } }%%")
    mermaid_lines.append("graph TD")
    
    # Extract nodes and connections, optionally add stats
    nodes = []
    connections = []
    for part in diagram_parts:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[0], str):
            nid, lbl = part
            
            # Add efficiency stats if requested
            if args.stats and samweb:
                stats = get_dataset_efficiency(lbl, samweb, max_files=args.max_files)
                if stats:
                    passed, generated, eff, num_files, is_extrapolated = stats
                    extrapolated_note = " (extrapolated)" if is_extrapolated else ""
                    lbl = f"{lbl}<br/>eff={eff:.4f}, trig: {passed}, gen: {generated}{extrapolated_note}<br/>nfiles={num_files}"
            
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
        
    # Use original input for output filename (dataset name or file name)
    dataset_name = '.'.join(args.filename.split('.')[:4])
    out_path = f"{dataset_name}.md"
    with open(out_path, 'w') as f:
        for line in mermaid_lines:
            f.write(line + '\n')
    print(f"Mermaid diagram saved to {out_path}")
    
    # Convert to PNG or SVG if requested
    if args.png or args.svg:
        import subprocess
        
        def convert_to_format(format_ext):
            output_path = f"{dataset_name}.{format_ext}"
            subprocess.run(['mmdc', '-i', out_path, '-o', output_path], check=True)
            # mmdc creates files with -1 suffix, so rename it
            actual_file = f"{dataset_name}-1.{format_ext}"
            if os.path.exists(actual_file):
                os.rename(actual_file, output_path)
            print(f"{format_ext.upper()} diagram saved to {output_path}")
        
        try:
            if args.png:
                convert_to_format('png')
            if args.svg:
                convert_to_format('svg')
        except subprocess.CalledProcessError as e:
            print(f"Error converting diagram: {e}")
        except FileNotFoundError:
            print("Error: mmdc command not found. Install with: npm install -g @mermaid-js/mermaid-cli")

if __name__ == '__main__':
    main()


