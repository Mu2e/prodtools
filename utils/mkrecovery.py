#!/usr/bin/env python3
"""Create recovery dataset definition for missing production files."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobiodetail import Mu2eJobIO
from samweb_wrapper import list_files, create_definition

if len(sys.argv) != 4:
    print("Usage: mkrecovery <tarball_path> <dataset> <njobs>")
    sys.exit(1)

tarball_path, dataset, njobs = sys.argv[1], sys.argv[2], int(sys.argv[3])

# Get expected output files from tarball for all job indices
job_io = Mu2eJobIO(tarball_path)
all_expected = set()
for i in range(njobs):
    outputs = job_io.job_outputs(i)
    all_expected.update(outputs.values())

# Filter to only files matching the dataset pattern
dataset_base = dataset.replace('.art', '')
expected_files = {f for f in all_expected if dataset_base in f}

# Get actual files from SAM
actual_files = set(list_files(f"dh.dataset {dataset}"))

# Find missing
missing_files = expected_files - actual_files

print(f"Missing: {len(missing_files)} of {len(expected_files)}")

if not missing_files:
    print("✅ No missing files!")
    sys.exit(0)

# Extract job indices from missing files
missing_indices = set()
for filename in missing_files:
    parts = filename.split('.')
    if len(parts) >= 5 and '_' in parts[4]:
        job_idx = int(parts[4].split('_')[1])
        missing_indices.add(job_idx)

# Create etc file list
etc_files = [f"etc.mu2e.index.000.{idx:07d}.txt" for idx in sorted(missing_indices)]

# Create SAM definition for recovery
defname = f"{dataset.replace('.art', '')}-recovery"
query = f"file_name in ({', '.join(etc_files)})"
if create_definition(defname, query):
    print(f"Created SAM definition: {defname}")
else:
    print(f"Failed to create (may already exist)")
