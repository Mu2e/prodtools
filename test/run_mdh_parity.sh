#!/bin/bash
# Run test/test_location.py with the mdh parity tests ACTIVE.
#
# The TestMdhParity cases skip unless `import mdh` works, and a full mdh
# import needs the ops spack environment (`muse setup ops`): plain
# python has no mdh, and the UPS route is broken on el9 (its python
# lacks gfal2 and needs el7 libssl.so.10). This wrapper enters that
# environment in a clean subshell, so it works from any shell —
# including one already muse-setup for SimJob — without disturbing it.
#
# Runs the test file directly, not `python3 -m unittest test.X`: the
# spack python resolves `test` to its own stdlib package.
set -euo pipefail
cd "$(dirname "$0")/.."

exec /bin/bash --noprofile --norc -c '
  # muse refuses a second setup in the same environment; scrub any
  # existing muse/spack state so this subshell starts clean.
  for v in $(compgen -e | grep -E "^(MUSE_|SPACK_)" || true); do unset "$v"; done
  source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
  muse setup ops
  exec python3 test/test_location.py "$@"
' -- "$@"
