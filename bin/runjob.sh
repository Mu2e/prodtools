#!/bin/bash
#
# runjob.sh — worker bootstrap for direct-mode runmu2e. jobsub_submit
# delivers this script (the release's or checkout's own copy) as the
# worker executable; the cnf tarball and ops JSON arrive via -f dropbox://
# under $CONDOR_DIR_INPUT. Prodtools reaches the job one of exactly two
# ways, both recorded on the campaign at enqueue so every slice and
# recovery runs identical code and the ledger says which:
#   MU2EGRID_PRODTOOLS_DIR  a cvmfs release, run in place (production);
#   MU2EGRID_PRODTOOLS_TAR  a dev checkout's prodtools/{bin,utils} tarball,
#                           shipped via -f dropbox:// and extracted under
#                           $_CONDOR_SCRATCH_DIR (opt-in, never mu2epro).
# jobsub copies the executable into the sandbox, so $0 cannot locate the
# code — the env var is the only path. Both set, or neither, is an error,
# not a cue to look elsewhere.
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
echo "MU2EGRID_PRODTOOLS_TAR=${MU2EGRID_PRODTOOLS_TAR:-unset}"
ls -la "$CONDOR_DIR_INPUT/" 2>&1 | head -10

echo "=== locating prodtools ==="
if [ -n "$MU2EGRID_PRODTOOLS_DIR" ] && [ -n "$MU2EGRID_PRODTOOLS_TAR" ]; then
    echo "ERROR: both MU2EGRID_PRODTOOLS_DIR and MU2EGRID_PRODTOOLS_TAR are set — a job runs a cvmfs release OR a shipped dev tarball, never both" >&2
    exit 1
fi
if [ -n "$MU2EGRID_PRODTOOLS_TAR" ]; then
    echo "=== extracting dev prodtools tarball $MU2EGRID_PRODTOOLS_TAR ==="
    tar xf "$CONDOR_DIR_INPUT/$MU2EGRID_PRODTOOLS_TAR" -C "$_CONDOR_SCRATCH_DIR" \
        || { echo "ERROR: tar xf $CONDOR_DIR_INPUT/$MU2EGRID_PRODTOOLS_TAR failed" >&2; exit 1; }
    MU2EGRID_PRODTOOLS_DIR="$_CONDOR_SCRATCH_DIR/prodtools"
fi
if [ -z "$MU2EGRID_PRODTOOLS_DIR" ]; then
    echo "ERROR: MU2EGRID_PRODTOOLS_DIR is not set and no MU2EGRID_PRODTOOLS_TAR was shipped — this job was not submitted with a prodtools release" >&2
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
