#!/usr/bin/env python3
"""
Lightweight smoke tests for mu2e job tools.

Runs a few import checks and verifies --help for entry-point modules.

Usage:
  python3 run_smoke_tests.py
"""

import subprocess
import sys


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(res.stdout)
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        raise SystemExit(res.returncode)


def test_imports():
    print("[imports] testing utils imports...")
    try:
        from utils.prod_utils import replace_file_extensions
        from utils import mixing_utils as mu
        from utils.jobdef import create_jobdef

    except Exception as e:
        print(f"Import failed: {e}")
        raise
    assert replace_file_extensions("a.b.c", "x", "y") == "x.b.y"
    assert "mubeam" in mu.PILEUP_MIXERS
    print("[imports] OK\n")


def test_entry_help():
    print("[help] verifying entry modules --help...")
    modules = [
        "jobdefs_runner",
        "fcl_maker", 
        "jobdefs_expander",
        "json_expander",
    ]
    for m in modules:
        run([sys.executable, m + ".py", "--help"])
    
    # Test utility modules that have CLI functionality
    print("[help] testing utility modules with CLI...")
    utility_modules = [
        "utils/jobdef",
        "utils/jobfcl",
        "utils/jobquery",
        "utils/jobiodetail",
    ]
    for m in utility_modules:
        run([sys.executable, m + ".py", "--help"])
    print("[help] OK\n")


def main():
    test_imports()
    test_entry_help()
    print("All smoke tests passed.")


if __name__ == "__main__":
    main()


