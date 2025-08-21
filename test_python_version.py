#!/usr/bin/env python3
import sys
import os
import subprocess

print("=== PYTHON VERSION TEST ===")
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")
print(f"Python path: {sys.path[:3]}...")  # First 3 entries

# Check which python
try:
    result = subprocess.run(["which", "python"], capture_output=True, text=True)
    print(f"which python: {result.stdout.strip() if result.returncode == 0 else 'not found'}")
except Exception as e:
    print(f"Error checking which python: {e}")

# Check which python3
try:
    result = subprocess.run(["which", "python3"], capture_output=True, text=True)
    print(f"which python3: {result.stdout.strip() if result.returncode == 0 else 'not found'}")
except Exception as e:
    print(f"Error checking which python3: {e}")

# Check SPACK_PYTHON environment variable
spack_python = os.getenv('SPACK_PYTHON')
print(f"SPACK_PYTHON: {spack_python}")

# Check if SPACK_PYTHON exists
if spack_python:
    print(f"SPACK_PYTHON exists: {os.path.exists(spack_python)}")
    if os.path.exists(spack_python):
        try:
            result = subprocess.run([spack_python, "--version"], capture_output=True, text=True)
            print(f"SPACK_PYTHON version: {result.stdout.strip()}")
        except Exception as e:
            print(f"Error running SPACK_PYTHON: {e}")

print("=== END PYTHON TEST ===")
