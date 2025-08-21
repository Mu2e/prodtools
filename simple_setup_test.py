#!/usr/bin/env python3
import os, sys, subprocess

print("=== MINIMAL SETUP TEST ===")
print(f"PWD: {os.getcwd()}")

setup_cmd = "source /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020ba/setup.sh"
print(f"Running: {setup_cmd}")

try:
    subprocess.run(setup_cmd, shell=True, check=True)
    print("✅ Success")
    sys.exit(0)
except subprocess.CalledProcessError as e:
    print(f"❌ Failed: {e.returncode}")
    print(f"STDERR: {e.stderr}")
    sys.exit(1)
