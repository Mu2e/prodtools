#!/bin/bash
#
# runjob.sh — worker bootstrap for direct-mode runmu2e.
#
# Used by Phase 2 of the prodtools direct-submit driver. jobsub_submit
# delivers this script as the worker executable; the cnf tarball and
# ops JSON arrive via -f dropbox:// under $CONDOR_DIR_INPUT, and a
# prodtools tarball arrives the same way (so the worker can use our
# patched utils/runmu2e.py without depending on a cvmfs version).
#
# Direct mode is detected inside runmu2e.py via MU2EGRID_JOBDEF env.

set -x

echo "=== runjob.sh starting ==="
echo "PWD=$PWD"
echo "PROCESS=${PROCESS:-unset}"
echo "CONDOR_DIR_INPUT=${CONDOR_DIR_INPUT:-unset}"
echo "INPUT_TAR_DIR_LOCAL=${INPUT_TAR_DIR_LOCAL:-unset}"
echo "_CONDOR_SCRATCH_DIR=${_CONDOR_SCRATCH_DIR:-unset}"
echo "MU2EGRID_JOBDEF=${MU2EGRID_JOBDEF:-unset}"
echo "MU2EGRID_OPSJSON=${MU2EGRID_OPSJSON:-unset}"
ls -la "$CONDOR_DIR_INPUT/" 2>&1 | head -10

echo "=== sourcing setupmu2e-art.sh ==="
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
echo "=== muse setup ops ==="
muse setup ops
echo "=== setup OfflineOps ==="
setup OfflineOps || echo "WARNING: setup OfflineOps failed (continuing — direct mode does not require it)"

echo "=== extracting prodtools tarball ==="
PRODTOOLS_DIR="$_CONDOR_SCRATCH_DIR/prodtools"
# MU2EGRID_PRODTOOLS_TAR is the basename of the dropbox-shipped tarball.
# Default to plain "prodtools.tar" for the legacy hand-crafted submission path.
PRODTOOLS_TAR="${MU2EGRID_PRODTOOLS_TAR:-prodtools.tar}"
tar xf "$CONDOR_DIR_INPUT/$PRODTOOLS_TAR" -C "$_CONDOR_SCRATCH_DIR"
ls -la "$PRODTOOLS_DIR/utils/runmu2e.py" 2>&1

echo "=== exec python3 runmu2e.py ==="
exec python3 "$PRODTOOLS_DIR/utils/runmu2e.py" "$@"
