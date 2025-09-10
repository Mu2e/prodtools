#!/bin/bash
echo "🔧 Setting up prodtools environment..."

# Source Mu2e environment
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh

# Setup muse
muse setup ops
muse setup SimJob

# Add prodtools bin directory to PATH
PRODTOOLS_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export PATH="$PRODTOOLS_DIR:$PATH"

# Add prodtools to PYTHONPATH for imports
PRODTOOLS_ROOT="$(dirname "$PRODTOOLS_DIR")"
export PYTHONPATH="$PRODTOOLS_ROOT:$PYTHONPATH"

echo "✅ prodtools environment ready!"
echo "   - Mu2e environment sourced"
echo "   - Muse setup complete"
echo "   - prodtools commands available: json2jobdef, fcldump, runjobdef, etc."
