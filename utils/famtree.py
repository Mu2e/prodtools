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
import functools
import os

# Package-first, bare fallback: one module identity when loaded as
# utils.famtree (web dashboard, cron), but still works from bin/ stubs
# that put utils/ itself on the path.
try:
    from utils.samweb_wrapper import (file_lineage, first_file_in_definition,
                                      get_samweb_wrapper)
    from utils.genFilterEff import process_dataset
    from utils.job_common import Mu2eName
except ImportError:
    from samweb_wrapper import (file_lineage, first_file_in_definition,
                                get_samweb_wrapper)
    from genFilterEff import process_dataset
    from job_common import Mu2eName

@functools.lru_cache(maxsize=None)
def get_parents(file_name):
    """Parent files via samweb file-lineage, filtering out etc files.

    Memoized: diamond ancestry (mix inputs converging on one beam chain)
    revisits the same file — the SAM lineage call is paid once per file."""
    parents = file_lineage(file_name, 'parents')
    return [p for p in parents if not (p.startswith('etc.') and p.endswith('.txt'))]

def get_dataset_name(file_name):
    """Return dataset name (drop run_subrun part) for 6-field names"""
    return str(Mu2eName.parse(file_name).dataset)

def output_stem(name):
    """Stable output-file stem for a file/dataset name: drop the extension
    and sequencer. Shared with the web dashboard, which reconstructs this
    stem to locate famtree's .md output."""
    n = Mu2eName.parse(name)
    return f"{n.tier}.{n.owner}.{n.description}.{n.dsconf}"

def get_first_file_from_dataset(dataset_name):
    """Get the first file from a dataset name (without run/subrun)."""
    # Dataset names are SAM definitions; streamed read stops at one name.
    first = first_file_in_definition(dataset_name)
    if first is None:
        print(f"No files found for dataset: {dataset_name}")
    return first

def get_dataset_efficiency(dataset_name, samweb, max_files=10, verbosity=0):
    """Efficiency statistics for a dataset, via genFilterEff.process_dataset.

    Returns (passed_events, generated_events, efficiency, num_files,
    is_extrapolated), or None if unavailable. Counts are scaled to full
    dataset size when only a sample of max_files was read;
    is_extrapolated flags that case.
    """
    try:
        # Server-side count — avoids transferring the full name list.
        num_files_total = samweb.dataset_file_count(dataset_name)

        summary = process_dataset(
            dataset_name,
            samweb,
            chunk_size=100,
            max_files=max_files,
            verbosity=verbosity
        )

        # If fewer files were sampled than exist, extrapolate the counts
        # (the efficiency ratio itself doesn't change).
        if summary.nfiles > 0 and summary.nfiles < num_files_total:
            scale_factor = num_files_total / summary.nfiles
            extrapolated_passed = int(summary.passedevents * scale_factor)
            extrapolated_generated = int(summary.genevents * scale_factor)
            eff = summary.efficiency()
            return (extrapolated_passed, extrapolated_generated, eff, num_files_total, True)
        else:
            return (summary.passedevents, summary.genevents, summary.efficiency(), num_files_total, False)

    except Exception:
        # No gencount for this dataset, or other lookup failure.
        return None

def generate_mermaid_diagram(file_name, node_id=0):
    """Generate Mermaid diagram data for the family tree."""

    current_node = f"N{node_id}"
    node_id += 1

    nodes = [(current_node, get_dataset_name(file_name))]
    connections = []

    parents = get_parents(file_name)
    if not parents:
        return current_node, node_id, nodes

    # Group parents by dataset, keeping one representative per dataset.
    dataset_to_parent = {}
    for parent in parents:
        dataset = get_dataset_name(parent)
        dataset_to_parent.setdefault(dataset, parent)

    for parent in dataset_to_parent.values():
        parent_node, node_id, parent_data = generate_mermaid_diagram(parent, node_id)
        if parent_node:
            # Arrow points parent -> child, toward N0.
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
    
    # Dataset name (5-field, no run/subrun) vs individual file (6-field).
    name = Mu2eName.parse(args.filename)

    if name.is_dataset:
        print(f"Dataset name detected: {args.filename}")
        actual_file = get_first_file_from_dataset(args.filename)
        if not actual_file:
            return
        print(f"Using first file: {actual_file}")
        file_to_process = actual_file
    elif name.is_file or name.is_tarball:
        file_to_process = args.filename
    else:
        print(f"Invalid filename format: {args.filename}. Expected 5 fields (dataset) or 6 fields (file).")
        return

    _, _, diagram_parts = generate_mermaid_diagram(file_to_process)

    if not diagram_parts:
        print("No family tree found for the given file.")
        return

    samweb = None
    if args.stats:
        samweb = get_samweb_wrapper()
        print("Fetching efficiency statistics...")

    mermaid_lines = []
    mermaid_lines.append("```mermaid")
    # Bold labels via HTML labels + loose security; wide wrappingWidth
    # avoids line wrapping.
    mermaid_lines.append("%%{init: { 'theme': 'forest', 'flowchart': { 'htmlLabels': true, 'wrappingWidth': 9999 }, 'securityLevel': 'loose' } }%%")
    mermaid_lines.append("graph TD")

    nodes = []
    connections = []
    # Shared ancestors appear as several node tuples with the same dataset
    # label — compute each label's stats (2 SAM queries + N metadata) once.
    stats_cache = {}
    for part in diagram_parts:
        if isinstance(part, tuple) and len(part) == 2 and isinstance(part[0], str):
            nid, lbl = part

            if args.stats and samweb:
                if lbl not in stats_cache:
                    stats_cache[lbl] = get_dataset_efficiency(lbl, samweb, max_files=args.max_files)
                stats = stats_cache[lbl]
                if stats:
                    passed, generated, eff, num_files, is_extrapolated = stats
                    extrapolated_note = " (extrapolated)" if is_extrapolated else ""
                    lbl = f"{lbl}<br/>eff={eff:.4f}, trig: {passed}, gen: {generated}{extrapolated_note}<br/>nfiles={num_files}"
            
            nodes.append(f'    {nid}["{lbl}"]')
        elif isinstance(part, str):
            connections.append(part)

    mermaid_lines.extend(nodes)
    if connections:
        mermaid_lines.append("")
        mermaid_lines.extend(connections)

    # Black-and-white styling for all nodes.
    mermaid_lines.append("")
    mermaid_lines.append("    classDef mainFile stroke-width:3px,font-size:16px")
    mermaid_lines.append("    classDef boldLabel stroke-width:2px,font-size:16px")
    mermaid_lines.append("")
    mermaid_lines.append(f"    class N0 mainFile")
    mermaid_lines.append("    linkStyle default stroke-width:3px,stroke:#000000")

    all_nodes = [n[0] for n in diagram_parts if isinstance(n, tuple) and n[0] != 'N0']
    if all_nodes:
        mermaid_lines.append(f"    class {','.join(all_nodes)} boldLabel")
    mermaid_lines.append("```")

    # Stable stem from the original input, dropping ext + sequencer.
    stem = output_stem(args.filename)
    out_path = f"{stem}.md"
    with open(out_path, 'w') as f:
        for line in mermaid_lines:
            f.write(line + '\n')
    print(f"Mermaid diagram saved to {out_path}")

    if args.png or args.svg:
        import subprocess

        def convert_to_format(format_ext):
            output_path = f"{stem}.{format_ext}"
            subprocess.run(['mmdc', '-i', out_path, '-o', output_path], check=True)
            # mmdc names its output with a -1 suffix; rename to match.
            actual_file = f"{stem}-1.{format_ext}"
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


