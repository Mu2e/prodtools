#!/bin/bash
#
# runjob.sh — worker bootstrap for direct-mode runmu2e. jobsub_submit
# delivers this script (the release's own copy) as the worker executable;
# the cnf tarball and ops JSON arrive via -f dropbox:// under
# $CONDOR_DIR_INPUT. Prodtools itself is NOT shipped: the job runs the
# cvmfs release named by MU2EGRID_PRODTOOLS_DIR, the same one recorded on
# the campaign at enqueue, so every slice and recovery runs identical
# code and the ledger says which. jobsub copies the executable into the
# sandbox, so $0 cannot locate the release — the env var is the only
# path, and its absence is an error, not a cue to look elsewhere.
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
echo "MU2EGRID_PRODTOOLS_DIR=${MU2EGRID_PRODTOOLS_DIR:-unset}"
ls -la "$CONDOR_DIR_INPUT/" 2>&1 | head -10

echo "=== locating prodtools release ==="
if [ -z "$MU2EGRID_PRODTOOLS_DIR" ]; then
    echo "ERROR: MU2EGRID_PRODTOOLS_DIR is not set — this job was not submitted with a prodtools release" >&2
    exit 1
fi
if [ ! -f "$MU2EGRID_PRODTOOLS_DIR/bin/setup.sh" ] || [ ! -f "$MU2EGRID_PRODTOOLS_DIR/utils/runmu2e.py" ]; then
    # A version published within the hour may not have reached this
    # worker's cvmfs catalog yet; failing here is what lets recovery
    # re-fire the index elsewhere later.
    echo "ERROR: $MU2EGRID_PRODTOOLS_DIR is not a prodtools release on this worker (bin/setup.sh or utils/runmu2e.py missing)" >&2
    ls -la "$MU2EGRID_PRODTOOLS_DIR" "$MU2EGRID_PRODTOOLS_DIR/bin" 2>&1 | head -20
    exit 1
fi

echo "=== sourcing setupmu2e-art.sh ==="
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
echo "=== muse setup ops ==="
muse setup ops
echo "=== setup OfflineOps ==="
setup OfflineOps || echo "WARNING: setup OfflineOps failed (continuing — direct mode does not require it)"
echo "=== source prodtools setup.sh ==="
source "$MU2EGRID_PRODTOOLS_DIR/bin/setup.sh"

echo "=== exec python3 runmu2e.py ==="
exec python3 "$MU2EGRID_PRODTOOLS_DIR/utils/runmu2e.py" "$@"
