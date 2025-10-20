#!/usr/bin/env python3
"""Create recovery dataset definition for missing production files."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobiodetail import Mu2eJobIO
from samweb_wrapper import list_files, create_definition

if len(sys.argv) != 4:
    print("Usage: mkrecovery <tarball_path> <dataset> <njobs>")
    print("\nExample:")
    print("  mkrecovery /pnfs/mu2e/.../cnf.mu2e.NeutralsFlash.MDC2025ac.0.tar \\")
    print("             dts.mu2e.EarlyNeutralsFlash.MDC2025ac.art \\")
    print("             40000")
    sys.exit(1)

tarball_path, dataset, njobs = sys.argv[1], sys.argv[2], int(sys.argv[3])

# Get output pattern from tarball
job_io = Mu2eJobIO(tarball_path)
output_file = list(job_io.job_outputs(0).values())[0]
parts = output_file.split('.')
base_name = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}"
run_number = int(parts[4].split('_')[0])
extension = parts[5]

# Get actual files from SAM
actual_files = set(list_files(f"dh.dataset {dataset}"))

# Find missing
expected = {f"{base_name}.{run_number:06d}_{i:08d}.{extension}" for i in range(njobs)}
missing = expected - actual_files

print(f"Missing: {len(missing)} of {njobs}")

if not missing:
    print("✅ No missing files!")
    sys.exit(0)

etc_files = []
for filename in sorted(missing):
    job_idx = int(filename.split('.')[4].split('_')[1])
    etc_files.append(f"etc.mu2e.index.000.{job_idx:07d}.txt")

# Create SAM definition for recovery
defname = f"{dataset.replace('.art', '')}-recovery"
query = f"file_name in ({', '.join(etc_files)})"
if create_definition(defname, query):
    print(f"Created SAM definition: {defname}")
else:
    print(f"Failed to create (may already exist)")
