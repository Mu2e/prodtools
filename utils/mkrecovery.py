#!/usr/bin/env python3
"""Create recovery dataset definition for missing production files."""
import sys, os, json, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.jobquery import Mu2eJobPars
from utils.samweb_wrapper import (
    create_definition,
    dataset_file_count,
    files_in_dataset,
    q_dataset_files_named,
)
from utils.job_common import Mu2eName
from utils.file_resolver import sam_physical_path
from utils.poms_entry import tarball_of, njobs_of, firstjob_of

def find_missing_indices(tarball_path, dataset, njobs, firstjob=0):
    """Find job indices for missing files in a dataset.

    A windowed entry (firstjob > 0) covers cnf indices
    [firstjob, firstjob+njobs). Returned indices are WINDOW-RELATIVE
    (0-based slot within the entry) so callers can map them to global
    recovery indices with a plain `cumulative + idx` — no caller does
    offset arithmetic.
    """
    job_io = Mu2eJobPars(tarball_path)
    # Build mapping from filename to window-relative index. Structured
    # compare — a substring test would false-match sibling dsconfs where
    # one is a prefix of the other (e.g. ..._v1_4 vs ..._v1_4-000).
    file_to_job = {}
    for job_idx in range(njobs):
        for filename in job_io.job_outputs(firstjob + job_idx).values():
            try:
                if str(Mu2eName.parse(filename).dataset) == dataset:
                    file_to_job[filename] = job_idx
            except ValueError:
                continue
    
    expected_files = set(file_to_job.keys())
    actual_files = set(files_in_dataset(dataset))
    missing_files = expected_files - actual_files
    
    if not missing_files:
        return set(), missing_files
    
    # Get unique job indices for missing files
    missing_indices = {file_to_job[f] for f in missing_files}
    return missing_indices, missing_files

def print_indices(tarball, firstjob, missing_indices):
    """Emit ABSOLUTE cnf indices, one per line, for `submit_map --indices-file`.

    `find_missing_indices` returns WINDOW-RELATIVE indices, so the absolute cnf
    index is `firstjob + relative`. NOTE this is a different index space from
    the recovery SAM definition, which carries GLOBAL indices (cumulative +
    relative) for the POMS `fname` path — direct-backend `--indices` wants cnf
    indices, POMS wants global ones.

    The `# <tarball>` header keeps a multi-entry dump attributable (indices only
    mean anything against their own cnf) and is skipped by the --indices-file
    parser.
    """
    print(f"# {tarball}")
    for idx in sorted(missing_indices):
        print(firstjob + idx)


def create_recovery_definition(defname, indices):
    """Create SAM recovery definition from job indices. Returns True on
    success; on failure prints the error and returns False (does not
    re-raise — caller can decide whether to abort the recovery flow)."""
    etc_files = [str(Mu2eName.build(tier='etc', owner='mu2e', description='index',
                                    dsconf='000', sequencer=f"{idx:07d}", extension='txt'))
                 for idx in sorted(indices)]
    query = q_dataset_files_named("etc.mu2e.index.000.txt", etc_files)
    try:
        create_definition(defname, query)
    except Exception as e:
        print(f"Failed to create SAM definition {defname}: {e}")
        return False
    print(f"Created SAM definition: {defname}")
    return True

def locate_tarball(tarball):
    """Locate and return full path to tarball, or None if SAM has no
    usable location (matches the old swallow-and-skip semantics)."""
    try:
        return sam_physical_path(tarball)
    except Exception:
        return None

def extract_datasets_from_tarball(tarball_path, njobs):
    """Extract output dataset names from job definition tarball."""
    job_pars = Mu2eJobPars(tarball_path)
    output_datasets = job_pars.output_datasets()
    
    # If output_datasets is empty, extract from actual output files
    if not output_datasets:
        dataset_set = set()
        for idx in range(min(10, njobs)):
            for filename in job_pars.job_outputs(idx).values():
                # Extract dataset name from filename (force .art extension to
                # match historical behavior — outputs may have other exts).
                try:
                    n = Mu2eName.parse(filename)
                except ValueError:
                    continue
                dataset_set.add(str(n.with_extension('art').dataset))
        output_datasets = list(dataset_set)
    
    return output_datasets

def main():
    p = argparse.ArgumentParser(description='Create recovery dataset for missing files')
    p.add_argument('input', help='Tarball path or jobdesc JSON file')
    p.add_argument('--dataset', help='Dataset name (required for single tarball mode)')
    p.add_argument('--njobs', type=int, help='Number of jobs (required for single tarball mode)')
    p.add_argument('--firstjob', type=int, default=0,
                   help='Cnf-index window start for single tarball mode (default 0)')
    p.add_argument('--jobdesc', action='store_true', help='Process jobdesc JSON file with global indices')
    p.add_argument('--print-indices', action='store_true',
                   help='Print the missing ABSOLUTE cnf indices to stdout instead '
                        'of creating a SAM recovery definition (read-only — makes '
                        'no SAM writes). Feeds `submit_map --indices-file`. '
                        'Diagnostics go to stderr so stdout stays pipeable.')
    args = p.parse_args()

    # In --print-indices mode stdout carries ONLY indices, so every diagnostic
    # goes to stderr; otherwise both go to stdout as before.
    out = sys.stderr if args.print_indices else sys.stdout

    if args.jobdesc:
        # Process jobdesc JSON file
        with open(args.input) as f:
            entries = json.load(f)

        json_basename = os.path.basename(args.input).replace('.json', '')
        all_missing_indices, cumulative = set(), 0
        per_entry_absolute = []

        print(f"Processing {len(entries)} entries from {args.input}\n{'='*60}\n", file=out)
        
        for i, entry in enumerate(entries):
            tarball = tarball_of(entry)
            njobs = njobs_of(entry)
            firstjob = firstjob_of(entry)
            if njobs is None:
                raise ValueError(f"POMS entry {i} missing required field: 'njobs'")
            print(f'[{i+1}/{len(entries)}] {tarball}'
                  + (f' (window {firstjob}..{firstjob + njobs - 1})' if firstjob else ''),
                  file=out)

            # Locate tarball
            tarball_path = locate_tarball(tarball)
            if not tarball_path or not os.path.exists(tarball_path):
                print(f'  ERROR: Could not locate tarball', file=out)
                cumulative += njobs
                continue

            # Extract output datasets from job definition
            try:
                output_datasets = extract_datasets_from_tarball(tarball_path, njobs)
            except Exception as e:
                print(f'  WARNING: Could not extract datasets from tarball: {e}', file=out)
                cumulative += njobs
                continue

            if not output_datasets:
                print(f'  WARNING: No output datasets found in job definition', file=out)
                cumulative += njobs
                continue

            # Process each dataset
            for dataset_name in output_datasets:
                try:
                    nfiles = dataset_file_count(dataset_name)
                except Exception as e:
                    print(f'    {dataset_name}: Could not query SAM ({e})', file=out)
                    nfiles = 0

                print(f'    {dataset_name}: {nfiles}/{njobs} files', file=out)
                missing_indices, missing_files = find_missing_indices(tarball_path, dataset_name, njobs, firstjob)

                if not missing_indices:
                    print(f'      Complete', file=out)
                else:
                    print(f'      Missing: {len(missing_files)} files (expected {njobs}, found {nfiles})',
                          file=out)
                    # window-relative → global recovery indices
                    all_missing_indices.update(cumulative + idx for idx in missing_indices)
                    per_entry_absolute.append((tarball, firstjob, set(missing_indices)))

            cumulative += njobs
            print(file=out)

        if args.print_indices:
            # Absolute cnf indices, grouped per tarball (they are only
            # meaningful against their own cnf — feed one group per submit).
            for tarball, firstjob, missing in per_entry_absolute:
                print_indices(tarball, firstjob, missing)
            if not per_entry_absolute:
                print("No missing files across all entries!", file=out)
        elif all_missing_indices:
            print(f"{'='*60}\nCreating global recovery dataset\n{'='*60}")
            print(f"Total missing indices: {len(all_missing_indices)}")
            create_recovery_definition(f"{json_basename}-recovery", all_missing_indices)
        else:
            print("No missing files across all entries!")
    
    else:
        # Single tarball mode
        if not args.dataset or not args.njobs:
            p.error("--dataset and --njobs required for single tarball mode")
        
        missing_indices, missing_files = find_missing_indices(args.input, args.dataset, args.njobs, args.firstjob)
        print(f"Missing: {len(missing_files)} of {args.njobs}", file=out)

        if args.print_indices:
            if missing_indices:
                print_indices(args.input, args.firstjob, missing_indices)
            else:
                print("No missing files!", file=out)
        elif missing_indices:
            create_recovery_definition(f"{args.dataset.replace('.art', '')}-recovery", missing_indices)
        else:
            print("No missing files!")

if __name__ == '__main__':
    main()
