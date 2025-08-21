#!/usr/bin/env python3
"""
Script to create test folders and generate jobdef files from JSON configuration using Python json2jobdef.py.
"""

import os
import sys
import json
import argparse
from pathlib import Path

# File patterns for job definition outputs
JOBDEF_FILE_PATTERNS = ['cnf.*.0.tar', 'cnf.*.0.fcl']

def move_jobdef_files(target_dir: Path, description: str = ""):
    """Move job definition files to target directory using JOBDEF_FILE_PATTERNS."""
    try:
        import shutil
        
        # Create target directory if it doesn't exist
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Move job definition files using specific patterns
        for file_pattern in JOBDEF_FILE_PATTERNS:
            for file_path in Path.cwd().glob(file_pattern):
                if file_path.exists() and file_path.is_file():
                    try:
                        shutil.move(str(file_path), str(target_dir / file_path.name))
                        print(f"    Moved {file_path.name} to {target_dir.name}/")
                    except Exception as e:
                        print(f"    Warning: Could not move {file_path.name}: {e}")
                        
    except Exception as e:
        print(f"    Warning: Error moving files to {target_dir.name}: {e}")

def create_test_folders():
    """Create test/python and test/perl folders in the current directory."""
    
    # Define the test folders to create
    test_folders = [
        "test/python",
        "test/perl"
    ]
    
    for folder_path in test_folders:
        full_path = Path.cwd() / folder_path
        full_path.mkdir(parents=True, exist_ok=True)

def load_json_config(json_file):
    """Load JSON configuration file."""
    try:
        with open(json_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading JSON file {json_file}: {e}")
        return None

def create_jobdefs_python(json_file, output_dir="."):
    """Create jobdef files using Python json2jobdef.py and run Perl equivalents immediately."""
    try:
        import subprocess
        
        # Load the JSON file to get configurations
        configs = load_json_config(json_file)
        if not configs:
            return False
        
        success_count = 0
        total_count = len(configs)
        
        for i, config in enumerate(configs):
            desc = config.get('desc', f'config_{i}')
            dsconf = config.get('dsconf', 'default')
            
            print(f"Processing config {i+1}/{total_count}: {desc}")
            
            # Clean up any leftover temporary files from previous configurations
            cleanup_temp_files()
            
                               # Note: We no longer clean up test/perl directory to preserve all Perl files
            
            # Run json2jobdef.py for each configuration
            cmd = [
                'python3', 'json2jobdef.py',
                '--json', json_file,
                '--desc', desc,
                '--dsconf', dsconf,
                '--index', str(i),
                '--no-cleanup'
            ]
            
            if output_dir != ".":
                cmd.extend(['--output-dir', output_dir])
            
            print(f"  🐍 Python command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            print(f"    🔍 Debug: subprocess return code: {result.returncode}")
            if result.stderr:
                print(f"    🔍 Debug: stderr: {result.stderr.strip()}")
            
            if result.returncode == 0:
                # Check if jobdef files were actually created
                jobdef_files = list(Path.cwd().glob('cnf.*.0.tar')) + list(Path.cwd().glob('cnf.*.0.fcl'))
                
                print(f"    🔍 Debug: Found {len(jobdef_files)} jobdef files: {[f.name for f in jobdef_files]}")
                
                if jobdef_files:
                    success_count += 1
                    print(f"  ✅ Python: Created jobdef for: {desc}")
                    
                else:
                    print(f"  ❌ Failed to create jobdef for: {desc} (no files generated)")
                    continue
                
                # Extract and run Perl commands for comparison
                perl_cmds = extract_perl_commands(result.stdout, desc, i)
                if perl_cmds:
                    print(f"  🔄 Running Perl equivalents for: {desc}")
            else:
                print(f"  ❌ Failed to create jobdef for: {desc}")
                if result.stderr:
                    print(f"    Error: {result.stderr.strip()}")
        
        # Move all Python-generated jobdef files to test/python at the end
        if success_count > 0:
            print(f"  📁 Moving all generated jobdef files to test/python/")
            move_jobdef_files(Path.cwd() / "test/python", "Python")
        
        print(f"Python version: {success_count}/{total_count} jobdefs created successfully")
        return success_count > 0
            
    except Exception as e:
        print(f"Error running Python json2jobdef.py: {e}")
        return False



def cleanup_temp_files():
    """Clean up temporary files after both Python and Perl versions complete."""
    try:
        import os
        
        # Remove temporary files
        for temp_file in ['template.fcl', 'inputs.txt']:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                print(f"    Cleaned up {temp_file}")
                        
    except Exception as e:
        print(f"    Warning: Error cleaning up temp files: {e}")

def normalize_fcl_content(content):
    """Normalize FCL content by removing cosmetic differences."""
    normalized_lines = []
    
    for line in content:
        # Strip whitespace and skip empty lines
        stripped = line.strip()
        if not stripped:
            continue
            
        # Skip comment lines that are just separators
        if stripped.startswith('#') and ('---' in stripped or '===' in stripped):
            continue
            
        # Normalize the line
        normalized_lines.append(stripped)
    
    # Sort lines to ignore ordering differences
    return sorted(normalized_lines)

def compare_fcl_files(desc, dsconf):
    """Compare FCL files generated by Python vs Perl versions."""
    try:
        import filecmp
        import difflib
        
        python_fcl = Path.cwd() / "test/python" / f"cnf.mu2e.{desc}.{dsconf}.0.fcl"
        perl_fcl = Path.cwd() / "test/perl" / f"cnf.mu2e.{desc}.{dsconf}.0.fcl"
        
        if not python_fcl.exists():
            print(f"    ⚠️  Python FCL file not found: {python_fcl.name}")
            return False
            
        if not perl_fcl.exists():
            print(f"    ⚠️  Perl FCL file not found: {perl_fcl.name}")
            return False
        
        # Read file contents
        with open(python_fcl, 'r') as f:
            python_content = f.readlines()
        with open(perl_fcl, 'r') as f:
            perl_content = f.readlines()
        
        # Normalize content
        python_normalized = normalize_fcl_content(python_content)
        perl_normalized = normalize_fcl_content(perl_content)
        
        # Compare normalized content
        if python_normalized == perl_normalized:
            print(f"    ✅ FCL files are functionally identical (cosmetic differences ignored)")
            return True
        else:
            print(f"    🔍 FCL files have functional differences...")
            
            # Find actual differences in normalized content
            diff = list(difflib.unified_diff(
                python_normalized, perl_normalized,
                fromfile=f"Python (normalized): {python_fcl.name}",
                tofile=f"Perl (normalized): {perl_fcl.name}",
                lineterm=''
            ))
            
            if diff:
                print(f"    📊 Functional differences found ({len(diff)} lines):")
                for line in diff[:10]:  # Show first 10 differences
                    print(f"      {line}")
                if len(diff) > 10:
                    print(f"      ... and {len(diff) - 10} more differences")
            else:
                print(f"    ✅ FCL files are functionally identical")
            
            return False
            
    except Exception as e:
        print(f"    ❌ Error comparing FCL files: {e}")
        return False

def cleanup_perl_directory():
    """Clean up test/perl directory before each new configuration."""
    try:
        import shutil
        import os
        
        perl_dir = Path.cwd() / "test/perl"
        if perl_dir.exists():
            # Only remove files that might conflict with the current configuration
            # Keep all previously generated Perl files
            current_files = list(perl_dir.glob("*.tar"))
            current_files.extend(perl_dir.glob("*.fcl"))
            
            # Don't remove template.fcl as it might be needed
            for file_path in current_files:
                if file_path.name != "template.fcl":
                    file_path.unlink()
                    print(f"    Cleaned up {file_path.name} from test/perl/")
                        
    except Exception as e:
        print(f"    Warning: Error cleaning up test/perl directory: {e}")

def extract_perl_commands(output, desc, index):
    """Extract Perl commands from json2jobdef.py human-readable output."""
    try:
        perl_commands = []
        
        # Parse the human-readable output to extract Perl commands
        lines = output.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            
            # Look for mu2ejobdef commands
            if line.startswith('Python mu2ejobdef equivalent command:'):
                # Extract the command from the next line
                continue
            elif line.startswith('mu2ejobdef '):
                command = line
                perl_commands.append({
                    'type': 'mu2ejobdef',
                    'command': command,
                    'desc': desc
                })
            
            # Look for mu2ejobfcl commands
            elif line.startswith('Running Perl equivalent of: mu2ejobfcl '):
                # Extract the command from the line
                command = line.replace('Running Perl equivalent of: ', '')
                perl_commands.append({
                    'type': 'mu2ejobfcl',
                    'command': command,
                    'desc': desc,
                    'index': index
                })
        
        if perl_commands:
            return perl_commands
        else:
            print(f"    Warning: No Perl commands found in output for {desc}")
            return []
            
    except Exception as e:
        print(f"    Warning: Error extracting Perl commands for {desc}: {e}")
        return []

# Removed complex environment setup function - not needed

def run_perl_commands_simple(perl_commands):
    """Run Perl commands using the environment that was set up by json2jobdef.py."""
    try:
        import subprocess
        
        # Run Perl commands using the current environment (which should have FHICL_FILE_PATH set)
        for cmd_info in perl_commands:
            cmd_type = cmd_info['type']
            command = cmd_info['command']
            desc = cmd_info['desc']
            
            print(f"    Running Perl {cmd_type}: {desc}")
            print(f"      🐪 Perl command: {command}")
            
            # Execute the command using the current environment
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"      ✅ Success: {cmd_type}")
            else:
                print(f"      ❌ Failed: {cmd_type}")
                if result.stderr.strip():
                    print(f"        Error: {result.stderr.strip()}")
                    
    except Exception as e:
        print(f"    Error running Perl commands: {e}")

# Removed unused function run_perl_commands_immediate

# Removed unused function
    """Run Perl commands in test/perl folder."""
    try:
        import subprocess
        
        # Create test/perl directory if it doesn't exist
        perl_dir = Path.cwd() / "test/perl"
        perl_dir.mkdir(parents=True, exist_ok=True)
        
        # Change to perl directory
        original_dir = Path.cwd()
        os.chdir(perl_dir)
        
        try:
            success_count = 0
            total_count = len(perl_commands)
            
            for i, cmd_info in enumerate(perl_commands):
                cmd_type = cmd_info['type']
                command = cmd_info['command']
                desc = cmd_info['desc']
                index = cmd_info['index']
                
                print(f"Running Perl {cmd_type} {i+1}/{total_count}: {desc}")
                print(f"  Command: {command}")
                
                # Execute the command with current environment (no shell=True to avoid function definition issues)
                result = subprocess.run(command.split(), capture_output=True, text=True, env=os.environ.copy())
                
                if result.returncode == 0:
                    success_count += 1
                    print(f"  ✅ Success: {cmd_type}")
                    if result.stdout.strip():
                        print(f"    Output: {result.stdout.strip()}")
                else:
                    print(f"  ❌ Failed: {cmd_type}")
                    if result.stderr.strip():
                        print(f"    Error: {result.stderr.strip()}")
            
            print(f"Perl commands: {success_count}/{total_count} executed successfully")
            
        finally:
            # Change back to original directory
            os.chdir(original_dir)
            
    except Exception as e:
        print(f"Error running Perl commands: {e}")
        # Change back to original directory in case of error
        try:
            os.chdir(original_dir)
        except:
            pass

            return
        
        # Only compare job configuration FCL files: cnf.*.0.fcl
        python_fcls = list(python_dir.glob("cnf.*.0.fcl"))
        perl_fcls = list(perl_dir.glob("cnf.*.0.fcl"))
        
        print(f"\n📊 FCL File Comparison Summary")
        print(f"   Python FCL files: {len(python_fcls)}")
        print(f"   Perl FCL files: {len(perl_fcls)}")
        
        if len(python_fcls) != len(perl_fcls):
            print(f"   ⚠️  Mismatch in file counts!")
            print(f"   Missing Perl files: {len(python_fcls) - len(perl_fcls)}")
        
        identical_count = 0
        different_count = 0
        missing_count = 0
        
        # Get base names for comparison
        python_basenames = {f.stem: f for f in python_fcls}
        perl_basenames = {f.stem: f for f in perl_fcls}
        
        all_names = set(python_basenames.keys()) | set(perl_basenames.keys())
        
        for name in sorted(all_names):
            python_file = python_basenames.get(name)
            perl_file = perl_basenames.get(name)
            
            if not python_file:
                print(f"   ❌ {name}: Missing Python file")
                missing_count += 1
                continue
                
            if not perl_file:
                print(f"   ❌ {name}: Missing Perl file")
                missing_count += 1
                continue
            
            # Read and normalize file contents
            with open(python_file, 'r') as f:
                python_content = f.readlines()
            with open(perl_file, 'r') as f:
                perl_content = f.readlines()
            
            python_normalized = normalize_fcl_content(python_content)
            perl_normalized = normalize_fcl_content(perl_content)
            
            # Compare normalized content
            if python_normalized == perl_normalized:
                print(f"   ✅ {name}: Functionally identical")
                identical_count += 1
            else:
                print(f"   🔍 {name}: Functionally different")
                different_count += 1
        
        print(f"\n📈 Summary:")
        print(f"   ✅ Identical: {identical_count}")
        print(f"   🔍 Different: {different_count}")
        print(f"   ❌ Missing: {missing_count}")
        print(f"   📊 Total: {len(all_names)}")
        
        # Show a few examples of differences
        if different_count > 0:
            print(f"\n🔍 Sample differences (first 3):")
            diff_examples = 0
            for name in sorted(all_names):
                if diff_examples >= 3:
                    break
                    
                python_file = python_basenames.get(name)
                perl_file = perl_basenames.get(name)
                
                if python_file and perl_file:
                    # Read and normalize content
                    with open(python_file, 'r') as f:
                        python_content = f.readlines()
                    with open(perl_file, 'r') as f:
                        perl_content = f.readlines()
                    
                    python_normalized = normalize_fcl_content(python_content)
                    perl_normalized = normalize_fcl_content(perl_content)
                    
                    if python_normalized != perl_normalized:
                        print(f"   {name}:")
                        # Show first few lines of diff using normalized content
                        diff = list(difflib.unified_diff(
                            python_normalized, perl_normalized,
                            fromfile=f"Python (normalized): {name}",
                            tofile=f"Perl (normalized): {name}",
                            lineterm='',
                            n=3  # Context lines
                        ))
                        
                        for line in diff[:10]:  # Show first 10 diff lines
                            print(f"     {line}")
                        print()
                        diff_examples += 1
                    
    except Exception as e:
        print(f"❌ Error during FCL comparison: {e}")

def create_jobdefs_from_json(json_file, output_dir="."):
    """Create jobdef files for all configurations in JSON file using Python json2jobdef.py."""
    configs = load_json_config(json_file)
    if not configs:
        return False
    
    print(f"Processing {len(configs)} configurations from {json_file}")
    
    # Create jobdefs using Python version
    print("\n=== Python Version ===")
    python_success = create_jobdefs_python(json_file, output_dir)
    
    # Add comprehensive FCL comparison at the end
    if python_success:
        print(f"\n=== FCL Comparison ===")
        compare_all_fcl_files()
    
    return python_success
    
    args = parser.parse_args()
    
    # Always create test folders
    create_test_folders()
    
    # Create jobdefs if JSON file is provided
def main():
    """Main function to parse arguments and run the parity test."""
    parser = argparse.ArgumentParser(description="Test parity between Python and Perl mu2ejobtools")
    parser.add_argument("--json", help="JSON configuration file")
    parser.add_argument("--output-dir", default=".", help="Output directory for jobdefs")
    
    args = parser.parse_args()
    
    # Always create test folders
    create_test_folders()
    
    # Create jobdefs if JSON file is provided
    if args.json:
        if not create_jobdefs_from_json(args.json, args.output_dir):
            return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
