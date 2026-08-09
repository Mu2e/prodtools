"""Identity dispatch for the write server.

The ONLY place that knows how to become mu2epro, and the ONLY place
that enforces confirm. Kept apart from tools.py so the security-
critical logic is testable without MCP plumbing.
"""
import os
import subprocess

RUN_AS_VALUES = ('self', 'mu2epro')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

# Verbatim from .claude/commands/mu2epro-submit.md:121-133. Every line
# here is a known failure mode, not a style preference:
#   - mktemp INSIDE ksu: a caller-owned workdir makes
#     condor_vault_storer fail
#   - USER/LOGNAME/HOME: ksu does not reset them, so getpass.getuser()
#     would return the caller and pick the wrong tarball and role
#   - XDG_RUNTIME_DIR: the caller's /run/user/<uid> is not writable by
#     mu2epro
#   - unset MUSE_WORK_DIR only: unsetting MUSE_* breaks the muse shell
#     function itself
_KSU_TEMPLATE = """
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_mcp.XXXXXX)
export XDG_RUNTIME_DIR="$WORKDIR"
cd "$WORKDIR"
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
muse setup ops > /dev/null 2>&1
setup OfflineOps > /dev/null 2>&1
{command}
"""


def require_confirmed(run_as, confirm):
    """Refuse a production write that was not explicitly confirmed.

    This gate lives in the call signature so it cannot be configured
    away. The PreToolUse hook is the second, independent gate; a hook
    can be un-armed by a settings reload, and an irreversible action
    must not depend on that.
    """
    if run_as not in RUN_AS_VALUES:
        raise ValueError(
            f"run_as must be one of {RUN_AS_VALUES}, got {run_as!r}")
    if run_as == 'mu2epro' and not confirm:
        raise PermissionError(
            "run_as='mu2epro' registers artifacts in production SAM and "
            "submits production grid jobs. This is not reversible. Pass "
            "confirm=true to proceed.")


def ksu_wrapper(argv):
    """Wrap a repo-relative argv in the full working ksu block."""
    command = ' '.join(
        [f'bash {REPO_ROOT}/{argv[0]}'] +
        [_quote(a) for a in argv[1:]])
    return ['ksu', 'mu2epro', '-e', '/bin/bash', '-c',
            _KSU_TEMPLATE.format(command=command)]


def _quote(arg):
    return "'" + str(arg).replace("'", "'\\''") + "'"


def run_cli(argv, run_as, cwd=None):
    """Run a prodtools command under the requested identity.

    Credentials are NEVER remediated. A missing mu2epro token comes back
    as a non-zero rc with its stderr intact; no refresh is attempted,
    ever.
    """
    if run_as == 'mu2epro':
        cmd = ksu_wrapper(argv)
    else:
        cmd = [f'bash', f'{REPO_ROOT}/{argv[0]}'] + [str(a) for a in argv[1:]]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd or REPO_ROOT)
    return {'rc': proc.returncode, 'stdout': proc.stdout,
            'stderr': proc.stderr}
