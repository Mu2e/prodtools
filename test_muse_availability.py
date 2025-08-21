#!/usr/bin/env python3
import os, sys, subprocess

print("=== MUSE AVAILABILITY TEST ===")
print(f"PWD: {os.getcwd()}")

# Check MUSE_DIR
muse_dir = os.getenv('MUSE_DIR')
print(f"MUSE_DIR: {muse_dir}")

# Check if muse binary exists
if muse_dir:
    muse_bin = f"{muse_dir}/bin/muse"
    print(f"MUSE binary path: {muse_bin}")
    print(f"MUSE binary exists: {os.path.exists(muse_bin)}")
    
    # Test if muse is executable
    if os.path.exists(muse_bin):
        try:
            result = subprocess.run([muse_bin, "--help"], capture_output=True, text=True, timeout=10)
            print(f"MUSE binary executable: ✅")
            print(f"MUSE help output: {result.stdout[:200]}...")
        except Exception as e:
            print(f"MUSE binary not executable: ❌ {e}")
else:
    print("MUSE_DIR not set")

# Check PATH for muse
print(f"\nPATH: {os.getenv('PATH', 'NOT SET')}")

# Try to find muse in PATH
try:
    result = subprocess.run(["which", "muse"], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"muse found in PATH: {result.stdout.strip()}")
    else:
        print("muse not found in PATH")
except Exception as e:
    print(f"Error checking PATH: {e}")

# Test the setup script with explicit muse path
if muse_dir and os.path.exists(f"{muse_dir}/bin/muse"):
    setup_cmd = f"source /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020ba/setup.sh"
    print(f"\nTesting setup with explicit muse path...")
    print(f"Running: {setup_cmd}")
    
    try:
        # Set PATH to include muse
        env = os.environ.copy()
        env['PATH'] = f"{muse_dir}/bin:{env.get('PATH', '')}"
        
        result = subprocess.run(setup_cmd, shell=True, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("✅ Setup succeeded with explicit muse path")
        else:
            print(f"❌ Setup failed with explicit muse path: {result.returncode}")
            print(f"STDERR: {result.stderr}")
    except Exception as e:
        print(f"❌ Error testing setup: {e}")

print("=== END TEST ===")
