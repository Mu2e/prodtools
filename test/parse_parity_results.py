#!/usr/bin/env python3
"""
Parse parity test results and generate a summary table
"""

import re
import sys

def parse_parity_output():
    """Parse the parity test output and create a summary table"""
    
    # Read the last parity test output
    try:
        with open('parity_test_output.txt', 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print("No parity test output found. Please run the parity test first.")
        return
    
    # Parse the output
    results = []
    current_config = None
    
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Look for configuration headers
        if '===' in line and '===' in line:
            current_config = line.strip('= ').strip()
            i += 1
            continue
            
        # Look for comparison results
        if 'Python vs' in line and 'identical?' in line:
            comparison_type = line.strip()
            
            # Look for the result (✅ or ❌) - it might be a few lines down due to diff output
            j = i + 1
            status = None
            while j < len(lines) and j < i + 10:  # Look within next 10 lines
                if lines[j].strip() == '✅':
                    status = 'IDENTICAL'
                    break
                elif lines[j].strip() == '❌':
                    status = 'DIFFERENT'
                    break
                elif '===' in lines[j] and '===' in lines[j]:  # Next config found
                    break
                j += 1
            
            if status and current_config:
                results.append({
                    'config': current_config,
                    'comparison': comparison_type,
                    'status': status
                })
        
        i += 1
    
    # Generate summary table
    print("## Parity Test Results Summary")
    print()
    print("| Configuration | Comparison Type | Status |")
    print("|---------------|-----------------|--------|")
    
    for result in results:
        config = result['config'].replace('cnf.mu2e.', '').replace('.0', '')
        comparison = result['comparison'].replace('Python vs ', '').replace(' identical?', '')
        status_icon = '✅' if result['status'] == 'IDENTICAL' else '❌'
        print(f"| {config} | {comparison} | {status_icon} {result['status']} |")
    
    # Summary statistics
    if results:
        identical_count = sum(1 for r in results if r['status'] == 'IDENTICAL')
        different_count = sum(1 for r in results if r['status'] == 'DIFFERENT')
        total_count = len(results)
        
        print()
        print("### Summary Statistics:")
        print(f"- **Total Comparisons**: {total_count}")
        print(f"- **Identical**: {identical_count} ({identical_count/total_count*100:.1f}%)")
        print(f"- **Different**: {different_count} ({different_count/total_count*100:.1f}%)")
        
        # Group by configuration
        print()
        print("### Results by Configuration:")
        configs = {}
        for result in results:
            config = result['config'].replace('cnf.mu2e.', '').replace('.0', '')
            if config not in configs:
                configs[config] = {'identical': 0, 'different': 0}
            if result['status'] == 'IDENTICAL':
                configs[config]['identical'] += 1
            else:
                configs[config]['different'] += 1
        
        for config, counts in configs.items():
            total = counts['identical'] + counts['different']
            success_rate = counts['identical'] / total * 100
            print(f"- **{config}**: {counts['identical']}/{total} identical ({success_rate:.1f}%)")

if __name__ == "__main__":
    parse_parity_output()
