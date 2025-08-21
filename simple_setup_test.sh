#!/bin/bash
echo "=== MINIMAL SETUP TEST ==="
echo "PWD: $(pwd)"

printenv

setup_cmd="source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh && source /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020ba/setup.sh"
echo "Running: $setup_cmd"

if $setup_cmd; then
    echo "✅ Success"
    exit 0
else
    echo "❌ Failed: $?"
    exit 1
fi
