#!/usr/bin/env python3
"""
Compare all Python vs Perl FCL files and provide a comprehensive summary.
"""

import filecmp
import difflib
from pathlib import Path

def compare_all_fcl_files():
    """Compare all Python vs Perl FCL files."""
    python_dir = Path("test/python")
    perl_dir = Path("test/perl")
    
    if not python_dir.exists() or not perl_dir.exists():
        print("❌ Test directories not found. Run create_test_folders.py first.")
        return
    
    python_fcls = list(python_dir.glob("*.fcl"))
    perl_fcls = list(perl_dir.glob("*.fcl"))
    
    print(f"📊 FCL File Comparison Summary")
    print(f"   Python FCL files: {len(python_fcls)}")
    print(f"   Perl FCL files: {len(perl_fcls)}")
    print()
    
    if len(python_fcls) != len(perl_fcls):
        print(f"⚠️  Mismatch in file counts!")
        print(f"   Missing Perl files: {len(python_fcls) - len(perl_fcls)}")
        print()
    
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
            print(f"❌ {name}: Missing Python file")
            missing_count += 1
            continue
            
        if not perl_file:
            print(f"❌ {name}: Missing Perl file")
            missing_count += 1
            continue
        
        # Compare files
        if filecmp.cmp(python_file, perl_file, shallow=False):
            print(f"✅ {name}: Identical")
            identical_count += 1
        else:
            print(f"🔍 {name}: Different")
            different_count += 1
    
    print()
    print(f"📈 Summary:")
    print(f"   ✅ Identical: {identical_count}")
    print(f"   🔍 Different: {different_count}")
    print(f"   ❌ Missing: {missing_count}")
    print(f"   📊 Total: {len(all_names)}")
    
    # Show a few examples of differences
    if different_count > 0:
        print()
        print(f"�� Sample differences (first 3):")
        diff_examples = 0
        for name in sorted(all_names):
            if diff_examples >= 3:
                break
                
            python_file = python_basenames.get(name)
            perl_file = perl_basenames.get(name)
            
            if python_file and perl_file and not filecmp.cmp(python_file, perl_file, shallow=False):
                print(f"   {name}:")
                # Show first few lines of diff
                with open(python_file, 'r') as f:
                    python_content = f.readlines()
                with open(perl_file, 'r') as f:
                    perl_content = f.readlines()
                
                diff = list(difflib.unified_diff(
                    python_content, perl_content,
                    fromfile=f"Python: {name}",
                    tofile=f"Perl: {name}",
                    lineterm='',
                    n=3  # Context lines
                ))
                
                for line in diff[:10]:  # Show first 10 diff lines
                    print(f"     {line}")
                print()
                diff_examples += 1

if __name__ == "__main__":
    compare_all_fcl_files()
