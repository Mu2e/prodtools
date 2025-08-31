#!/bin/bash
echo "🔧 Setting up Mu2e Ops Tools Environment..."

# Source the Mu2e environment
if [ -f /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh ]; then
    source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
    muse setup ops
    setup OfflineOps
    echo "✅ Sourced Mu2e environment"
else
    echo "❌ Warning: Mu2e environment not found"
fi

# Add prodtools and test directories to PATH
PRODTOOLS_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export PATH="$PRODTOOLS_DIR:$PATH"

echo "📁 Added prodtools directory to PATH: $PRODTOOLS_DIR"
echo "🚀 You can now run all commands directly:"
echo "   Production tools:"
echo "     - json2jobdef.py"
echo "     - fcl_maker.py"
echo "     - jobdefs_runner.py"
echo "     - json_expander.py"
echo ""
echo "💡 Usage examples:"
echo "   Production tools:"
echo "     json2jobdef.py --json config.json --index 0"
echo "     fcl_maker.py --dataset dts.mu2e.RPCExternal.MDC2020az.art"
echo "     jobdefs_runner.py --jobdefs jobdefs.txt --dry-run"
