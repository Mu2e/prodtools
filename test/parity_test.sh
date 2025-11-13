#!/bin/bash

# Parse command line arguments
RUN_ALL=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            RUN_ALL=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--all]"
            echo "  --all: Run all configurations (default: run only index 0)"
            exit 1
            ;;
    esac
done

if [ "$RUN_ALL" = true ]; then
    echo " Starting All Parity Tests..."
else
    echo " Starting Single Configuration Test (index 0)..."
fi

# Check if MUSE_WORK_DIR is set
if [ -z "$MUSE_WORK_DIR" ]; then
    echo "❌ Error: MUSE_WORK_DIR environment variable is not set."
    echo "Please run 'muse setup SimJob' first to set the environment."
    exit 1
fi

echo "📁 Using MUSE_WORK_DIR: $MUSE_WORK_DIR"

# Clean up previous test outputs to ensure clean comparison
echo "🧹 Cleaning up previous test outputs..."
rm -rf python/*.tar python/*.fcl perl/*.tar perl/*.fcl
echo "✅ Cleaned up previous test outputs"

if [ "$RUN_ALL" = true ]; then
    # Run all configurations
    echo "1. Stage1 Jobs..."
    python3 parity_test.py --json "../data/mdc2025/stage1.json"

    echo "2. Resampler Jobs..."
    python3 parity_test.py --json "../data/mdc2025/resampler_beam.json"

    echo "3. Mixing Jobs..."
    python3 parity_test.py --json "../data/mdc2025/mix2.json"

    echo "4. Merge/Filter Jobs..."
    python3 parity_test.py --json "../data/mdc2025/merge_filter.json"
else
    # Run only index 0 for each type
    echo "1. Stage1 Jobs (index 0)..."
    python3 parity_test.py --json "../data/mdc2025/stage1.json" --index 0

    echo "2. Resampler Jobs (index 0)..."
    python3 parity_test.py --json "../data/mdc2025/resampler_beam.json" --index 0

    echo "3. Mixing Jobs (index 0)..."
    python3 parity_test.py --json "../data/mdc2025/mix2.json" --index 0

    echo "4. Merge/Filter Jobs (index 0)..."
    python3 parity_test.py --json "../data/mdc2025/merge_filter.json" --index 0
fi

echo "5. Running comparison..."
./compare_tarballs.sh

if [ "$RUN_ALL" = true ]; then
    echo " All tests completed!"
else
    echo " Single configuration tests completed!"
fi



# To test individual configurations instead of all, use --index option:
# Example: python3 parity_test.py --json "$MUSE_WORK_DIR/prodtools/data/mix.json" --index 0
# Example: python3 parity_test.py --json "$MUSE_WORK_DIR/prodtools/data/stage1.json" --index 2
