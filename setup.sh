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
echo "     - json2jobdef"
echo "     - fcldump"
echo "     - jobrunner"
echo "     - mkidxdef"
echo "     - jobdef"
echo "     - jsonexpander"
echo ""
echo "💡 Usage examples:"
echo "   Production tools:"
echo "     muse setup SimJob"
echo "     json2jobdef --json config.json --index 0"
echo "     ./jobdef --setup /cvmfs/mu2e.opensciencegrid.org/Musings/SimJob/MDC2020av/setup.sh \\"
echo "         --dsconf MDC2020av --desc ExtractedCRY --dsowner mu2e --embed template.fcl"
echo "     fcldump --dataset cnf.mu2e.RPCInternalPhysical.MDC2020az.tar"
echo "     jobrunner --jobdefs jobdefs.txt --dry-run"
