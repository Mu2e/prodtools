#!/usr/bin/env python3
"""
Script to create test folders and generate jobdef files from JSON configuration using Python json2jobdef.py.
"""

import sys
import argparse
import subprocess
import shutil
import os
from pathlib import Path

# Add parent directory to path to import json2jobdef
sys.path.insert(0, str(Path(__file__).parent.parent))
from json2jobdef import process_single_entry, load_json, find_json_entry

# File patterns for job definition outputs
JOBDEF_FILE_PATTERNS = ['cnf.*.0.tar', 'cnf.*.0.fcl']

def move_jobdef_files(target_dir: Path):
    """Move job definition files to target directory using JOBDEF_FILE_PATTERNS."""
    # Create target directory if it doesn't exist
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # Move job definition files using specific patterns
    for file_pattern in JOBDEF_FILE_PATTERNS:
        for file_path in Path.cwd().glob(file_pattern):
            if file_path.exists() and file_path.is_file():
                shutil.move(str(file_path), str(target_dir / file_path.name))
                print(f"    Moved {file_path.name} to {target_dir.name}/")

def create_jobdef(config):
    """Create jobdef for a single configuration using json2jobdef."""
    
    # Call process_single_entry directly with JSON output
    result = process_single_entry(config, json_output=True, no_cleanup=True)
    
    if not result or not result.get('perl_commands'):
        print(f"  ⚠️  No mu2ejobdef commands found in result for: {config['desc']}")
        return False
    
    print(f"  📁 Moving Python files to python/ before running mu2ejobdef")
    move_jobdef_files(Path.cwd() / "python")
    create_mu2ejobdef(result['perl_commands'])
    
    return True

def create_mu2ejobdef(perl_commands):
    """Execute mu2ejobdef commands for a single configuration."""
    # Get the mu2ejobdef command (should be the first and only one)
    mu2ejobdef_cmd = [cmd for cmd in perl_commands if cmd['type'] == 'mu2ejobdef'][0]
    
    # Run the mu2ejobdef command
    command = mu2ejobdef_cmd['command']

    print(f"      🐪 mu2ejobdef command: {command}")
    
    # Don't source the setup script again since it's already sourced in the main shell
    result = subprocess.run(command, shell=True, capture_output=True, text=True, env=os.environ.copy())
    
    print(f"      Return code: {result.returncode}")
    print(f"      stderr: {result.stderr.strip()}")
    print(f"      stdout: {result.stdout.strip()}")
    
    if result.returncode != 0:
        print(f"      ❌ Error: {result.stderr.strip()}")
        if result.stdout.strip():
            print(f"      stdout: {result.stdout.strip()}")
        return False
    else:
        print(f"      ✅ mu2ejobdef completed successfully")
    
    # Only move files if mu2ejobdef succeeded
    move_jobdef_files(Path.cwd() / "perl")
    
    return True

def create_jobdefs_from_json(json_file, index=None):
    """Create jobdef files for configurations in JSON file using Python json2jobdef.py.
    
    Args:
        json_file: Path to JSON configuration file
        index: Optional index to process only one specific configuration
    """
    print(f"Processing configurations from {json_file}")
    configs = load_json(Path(json_file))
    total_count = len(configs)
    
    if index is not None:
        # Process only the specified index
        if index < 0 or index >= total_count:
            print(f"❌ Error: Index {index} is out of range. Valid range: 0-{total_count-1}")
            return False
        
        config = find_json_entry(configs=configs, index=index)
        print(f"Processing single configuration {index}: {config['desc']}")
        
        # Clean up template.fcl from previous iteration to avoid interference
        if Path('template.fcl').exists():
            Path('template.fcl').unlink()
            print(f"  🧹 Cleaned up template.fcl from previous configuration")
        
        if create_jobdef(config):
            print(f"✅ Successfully processed configuration {index}: {config['desc']}")
            return True
        else:
            print(f"❌ Failed to process configuration {index}: {config['desc']}")
            return False
    else:
        # Process all configurations
        success_count = 0
        for i in range(total_count):
            config = find_json_entry(configs=configs, index=i)
            
            # Clean up template.fcl from previous iteration to avoid interference
            if Path('template.fcl').exists():
                Path('template.fcl').unlink()
                print(f"  🧹 Cleaned up template.fcl from previous configuration")
            
            if create_jobdef(config):
                success_count += 1
        
        print(f"{success_count}/{total_count} jobdefs successfully processed")
        return True

def main():
    """Main function to parse arguments and run the parity test."""
    parser = argparse.ArgumentParser(
        description="Test parity between Python and mu2ejobdef tools",
        epilog="""
Examples:
  # Test all configurations in mix.json
  python parity_test.py --json ../../Production/data/mix.json
  
  # Test only the first configuration (index 0)
  python parity_test.py --json ../../Production/data/mix.json --index 0
  
  # Test only the third configuration (index 2)
  python parity_test.py --json ../../Production/data/mix.json --index 2
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", required=True, help="JSON configuration file")
    parser.add_argument("--index", type=int, help="Process only the specified configuration index (0-based). If not specified, processes all configurations.")
    
    args = parser.parse_args()
    
    # Create test folders needed for organizing Python vs mu2ejobdef output files
    for folder in ["python", "perl"]:
        Path(folder).mkdir(parents=True, exist_ok=True)
    
    if not create_jobdefs_from_json(args.json, args.index):
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
