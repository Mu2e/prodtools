#!/usr/bin/env python3
"""Create recovery dataset definition for missing production files."""
import sys, os, json, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.jobiodetail import Mu2eJobIO
from utils.samweb_wrapper import SAMWebWrapper, list_files, create_definition
from utils.job_common import remove_storage_prefix

def find_missing_indices(tarball_path, dataset, njobs):
    """Find job indices for missing files in a dataset."""
    job_io = Mu2eJobIO(tarball_path)
    all_expected = set()
    for i in range(njobs):
        all_expected.update(job_io.job_outputs(i).values())
    
    dataset_base = dataset.replace('.art', '')
    expected_files = {f for f in all_expected if dataset_base in f}
    actual_files = set(list_files(f"dh.dataset {dataset}"))
    missing_files = expected_files - actual_files
    
    if not missing_files:
        return set(), missing_files
    
    # Find which job index produces each missing file
    missing_indices = set()
    for filename in missing_files:
        for job_idx in range(njobs):
            if filename in job_io.job_outputs(job_idx).values():
                missing_indices.add(job_idx)
                break
    
    return missing_indices, missing_files

def create_recovery_definition(defname, indices):
    """Create SAM recovery definition from job indices."""
    etc_files = [f"etc.mu2e.index.000.{idx:07d}.txt" for idx in sorted(indices)]
    query = f"dh.dataset etc.mu2e.index.000.txt and file_name in ({', '.join(etc_files)})"
    result = create_definition(defname, query)
    print(f"Created SAM definition: {defname}" if result else f"Failed to create {defname} (may already exist)")
    return result

def locate_tarball(sam, tarball):
    """Locate and return full path to tarball."""
    locs = sam.locate_file_full(tarball)
    if not locs:
        return None
    
    # Use first (and only) location
    return os.path.join(remove_storage_prefix(locs[0].get('full_path', '')), tarball)

def main():
    p = argparse.ArgumentParser(description='Create recovery dataset for missing files')
    p.add_argument('input', help='Tarball path or jobdesc JSON file')
    p.add_argument('--dataset', help='Dataset name (required for single tarball mode)')
    p.add_argument('--njobs', type=int, help='Number of jobs (required for single tarball mode)')
    p.add_argument('--jobdesc', action='store_true', help='Process jobdesc JSON file with global indices')
    args = p.parse_args()
    
    if args.jobdesc:
        # Process jobdesc JSON file
        from utils.pomsMonitor import PomsMonitor
        
        with open(args.input) as f:
            entries = json.load(f)
        
        json_basename = os.path.basename(args.input).replace('.json', '')
        monitor, sam = PomsMonitor(), SAMWebWrapper()
        all_missing_indices, cumulative = set(), 0
        
        print(f"Processing {len(entries)} entries from {args.input}\n{'='*60}\n")
        
        for i, entry in enumerate(entries):
            tarball, njobs = entry['tarball'], entry['njobs']
            print(f'[{i+1}/{len(entries)}] {tarball}')
            
            # Locate tarball
            tarball_path = locate_tarball(sam, tarball)
            if not tarball_path or not os.path.exists(tarball_path):
                print(f'  ERROR: Could not locate tarball')
                cumulative += njobs
                continue
            
            # Process each dataset
            dataset_infos = monitor.get_output_datasets_with_counts(tarball)
            if not dataset_infos:
                print(f'  WARNING: Could not extract datasets')
                cumulative += njobs
                continue
            
            for dataset_name, nfiles, _, _ in dataset_infos:
                print(f'    {dataset_name}: {nfiles}/{njobs} files')
                missing_indices, missing_files = find_missing_indices(tarball_path, dataset_name, njobs)
                
                if not missing_indices:
                    print(f'      Complete')
                else:
                    print(f'      Missing: {len(missing_files)} of {nfiles} files')
                    all_missing_indices.update(cumulative + idx for idx in missing_indices)
            
            cumulative += njobs
            print()
        
        # Create global recovery definition
        if all_missing_indices:
            print(f"{'='*60}\nCreating global recovery dataset\n{'='*60}")
            print(f"Total missing indices: {len(all_missing_indices)}")
            create_recovery_definition(f"{json_basename}-recovery", all_missing_indices)
        else:
            print("No missing files across all entries!")
    
    else:
        # Single tarball mode
        if not args.dataset or not args.njobs:
            p.error("--dataset and --njobs required for single tarball mode")
        
        missing_indices, missing_files = find_missing_indices(args.input, args.dataset, args.njobs)
        print(f"Missing: {len(missing_files)} of {args.njobs}")
        
        if missing_indices:
            create_recovery_definition(f"{args.dataset.replace('.art', '')}-recovery", missing_indices)
        else:
            print("No missing files!")

if __name__ == '__main__':
    main()
