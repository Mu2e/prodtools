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
    dataset_base = dataset.replace('.art', '')
    
    # Build mapping from filename to job index
    file_to_job = {}
    for job_idx in range(njobs):
        for filename in job_io.job_outputs(job_idx).values():
            if dataset_base in filename:
                file_to_job[filename] = job_idx
    
    expected_files = set(file_to_job.keys())
    actual_files = set(list_files(f"dh.dataset {dataset}"))
    missing_files = expected_files - actual_files
    
    if not missing_files:
        return set(), missing_files
    
    # Get unique job indices for missing files
    missing_indices = {file_to_job[f] for f in missing_files}
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
    return os.path.join(remove_storage_prefix(locs[0].get('full_path', '')), tarball)

def extract_datasets_from_tarball(tarball_path, njobs):
    """Extract output dataset names from job definition tarball."""
    from utils.jobquery import Mu2eJobPars
    
    job_pars = Mu2eJobPars(tarball_path)
    output_datasets = job_pars.output_datasets()
    
    # If output_datasets is empty, extract from actual output files
    if not output_datasets:
        job_io = Mu2eJobIO(tarball_path)
        dataset_set = set()
        for idx in range(min(10, njobs)):
            for filename in job_io.job_outputs(idx).values():
                # Extract dataset name from filename (e.g., dig.mu2e.Beam.MDC2020bc.art)
                parts = filename.split('.')
                if len(parts) >= 4:
                    dataset_set.add('.'.join(parts[:4]) + '.art')
        output_datasets = list(dataset_set)
    
    return output_datasets

def main():
    p = argparse.ArgumentParser(description='Create recovery dataset for missing files')
    p.add_argument('input', help='Tarball path or jobdesc JSON file')
    p.add_argument('--dataset', help='Dataset name (required for single tarball mode)')
    p.add_argument('--njobs', type=int, help='Number of jobs (required for single tarball mode)')
    p.add_argument('--jobdesc', action='store_true', help='Process jobdesc JSON file with global indices')
    args = p.parse_args()
    
    if args.jobdesc:
        # Process jobdesc JSON file
        from utils.samweb_wrapper import count_files
        
        with open(args.input) as f:
            entries = json.load(f)
        
        json_basename = os.path.basename(args.input).replace('.json', '')
        sam = SAMWebWrapper()
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
            
            # Extract output datasets from job definition
            try:
                output_datasets = extract_datasets_from_tarball(tarball_path, njobs)
            except Exception as e:
                print(f'  WARNING: Could not extract datasets from tarball: {e}')
                cumulative += njobs
                continue
            
            if not output_datasets:
                print(f'  WARNING: No output datasets found in job definition')
                cumulative += njobs
                continue
            
            # Process each dataset
            for dataset_name in output_datasets:
                try:
                    nfiles = count_files(f"dh.dataset {dataset_name}")
                except Exception as e:
                    print(f'    {dataset_name}: Could not query SAM ({e})')
                    nfiles = 0
                
                print(f'    {dataset_name}: {nfiles}/{njobs} files')
                missing_indices, missing_files = find_missing_indices(tarball_path, dataset_name, njobs)
                
                if not missing_indices:
                    print(f'      Complete')
                else:
                    print(f'      Missing: {len(missing_files)} files (expected {njobs}, found {nfiles})')
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
