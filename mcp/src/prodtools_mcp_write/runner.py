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

# Defence in depth alongside the shell-quoting in ksu_wrapper: even a
# correctly quoted argv[0] should never be allowed to name an arbitrary
# repo script, because quoting only stops shell injection, not "run a
# script this server was never meant to expose" as mu2epro.
ALLOWED_ENTRY_POINTS = frozenset({
    'bin/json2jobdef',
    'bin/submit_map',
    'bin/submissions',
})

# Adapted from the ksu block in .claude/commands/mu2epro-submit.md
# (mktemp workdir, USER/LOGNAME/HOME exports, XDG_RUNTIME_DIR, unset
# MUSE_WORK_DIR only) — NOT byte-for-byte verbatim: this template adds
# `setup OfflineOps` (not present in that source block) and drops its
# `RC=${PIPESTATUS[0]}; exit $RC` tail (benign here — the exit status
# still propagates through `ksu -e`'s own return code). Every exported
# variable and the mktemp/cd/source sequence below is still a known
# failure mode, not a style choice:
#   - mktemp INSIDE ksu: a caller-owned workdir makes
#     condor_vault_storer fail
#   - USER/LOGNAME/HOME: ksu does not reset them, so getpass.getuser()
#     would return the caller and pick the wrong tarball and role
#   - XDG_RUNTIME_DIR: the caller's /run/user/<uid> is not writable by
#     mu2epro
#   - unset MUSE_WORK_DIR only: unsetting MUSE_* breaks the muse shell
#     function itself (it needs MUSE_DIR to stay set)
#   - the setup lines are chained with && (not run as separate
#     statements): a failed CVMFS source or `muse setup ops` must abort
#     the command, not silently run it in a broken environment
#
# `setupmu2e-art.sh && muse setup ops && setup OfflineOps` alone leaves
# MUSE_DIR set but `mu2e` NOTFOUND and MU2E_SEARCH_PATH empty:
# `bin/json2jobdef` hard-exits without a Musing on top (see its own
# `command -v mu2e` guard). `{musing_clause}` sources the entry's own
# `simjob_setup` — the same "source the full setup.sh path" mechanism
# .claude/commands/mu2e-run.md uses for any Musing, SimJob or not — so
# the caller derives the release from the JSON config instead of
# passing a tag that could silently disagree with it.
#
# Every step redirects its own stdout/stderr to /dev/null (CVMFS setup
# scripts are chatty), so a bare `&&` chain would abort on failure with
# rc != 0 but EMPTY stdout/stderr — push_cnf would raise
# `RuntimeError("json2jobdef failed (rc=1): ")` with nothing to debug.
# Each `|| { echo ... >&2; exit 1; }` names which step failed on stderr
# before exiting, so `run_cli`'s captured stderr is never empty on a
# setup failure. The braces are doubled (`{{` `}}`) because this is a
# str.format() TEMPLATE — {musing_clause} and {command} are its only
# real placeholders.
_SETUP_CHAIN = """source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \\
  || {{ echo 'push_cnf: setupmu2e-art.sh failed' >&2; exit 1; }} \\
  && muse setup ops > /dev/null 2>&1 \\
  || {{ echo 'push_cnf: muse setup ops failed' >&2; exit 1; }} \\
  && setup OfflineOps > /dev/null 2>&1 \\
  || {{ echo 'push_cnf: setup OfflineOps failed' >&2; exit 1; }} \\
  && {musing_clause}{command}"""

_KSU_TEMPLATE = """
unset MUSE_WORK_DIR
export USER=mu2epro LOGNAME=mu2epro HOME=/exp/mu2e/app/home/mu2epro
WORKDIR=$(mktemp -d /tmp/mu2epro_mcp.XXXXXX) || exit 1
export XDG_RUNTIME_DIR="$WORKDIR"
cd "$WORKDIR"
{chain}
"""

# The identity-specific header above (mktemp workdir, USER/LOGNAME/HOME,
# XDG_RUNTIME_DIR) exists only to work around ksu quirks (see the
# bullet list above); running as yourself needs none of it — cwd,
# USER and XDG_RUNTIME_DIR are already correct. `unset MUSE_WORK_DIR`
# is kept because it clears calling-environment state, not a ksu
# quirk. The setup chain itself — CVMFS source, muse setup ops,
# OfflineOps, the Musing — is identical between the two identities;
# the only difference is the ksu wrapping.
_SELF_TEMPLATE = """
unset MUSE_WORK_DIR
{chain}
"""


def _musing_clause(simjob_setup):
    """`&&`-chained `source <simjob_setup>` step, or '' when not given.

    Quoted with `_quote` like every other interpolated word — a
    hostile `simjob_setup` value must not be able to escape it. Also
    announces its own failure on stderr instead of swallowing it into
    `/dev/null` like the base setup steps (see _SETUP_CHAIN) -- kept as
    a static message rather than echoing the quoted path itself, since
    embedding an already-quoted value inside a second, differently-
    quoted echo string would reopen the same escaping problem `_quote`
    exists to close.
    """
    if not simjob_setup:
        return ''
    return (f"source {_quote(simjob_setup)} > /dev/null 2>&1 "
            "|| { echo 'push_cnf: Musing setup failed' >&2; exit 1; } \\\n  && ")


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


def _validate_entry_point(argv0):
    """Reject any argv[0] this server is not explicitly meant to run.

    Quoting (see _quote/ksu_wrapper) makes shell injection impossible;
    this is the separate, independent control that stops a caller
    naming an arbitrary-but-otherwise-harmless repo script and having
    it run as mu2epro.
    """
    if argv0 not in ALLOWED_ENTRY_POINTS:
        raise ValueError(
            f"{argv0!r} is not an allowed write-server entry point; "
            f"must be one of {sorted(ALLOWED_ENTRY_POINTS)}")


def _command_of(argv):
    """`bash '<repo-relative path>' 'arg' ...`, every word quoted."""
    _validate_entry_point(argv[0])
    return ' '.join(
        ['bash', _quote(os.path.join(REPO_ROOT, argv[0]))] +
        [_quote(a) for a in argv[1:]])


def ksu_wrapper(argv, simjob_setup=None):
    """Wrap a repo-relative argv in the full working ksu block."""
    chain = _SETUP_CHAIN.format(
        musing_clause=_musing_clause(simjob_setup), command=_command_of(argv))
    return ['ksu', 'mu2epro', '-e', '/bin/bash', '-c',
            _KSU_TEMPLATE.format(chain=chain)]


def _self_wrapper(argv, simjob_setup=None):
    """Wrap a repo-relative argv in the same setup chain as ksu_wrapper,
    minus the ksu-only identity header (see _SELF_TEMPLATE)."""
    chain = _SETUP_CHAIN.format(
        musing_clause=_musing_clause(simjob_setup), command=_command_of(argv))
    return ['bash', '-c', _SELF_TEMPLATE.format(chain=chain)]


def _quote(arg):
    return "'" + str(arg).replace("'", "'\\''") + "'"


def run_cli(argv, run_as, cwd=None, simjob_setup=None):
    """Run a prodtools command under the requested identity.

    `simjob_setup` is the full path to a Musing's `setup.sh` (the
    `simjob_setup` field of a json2jobdef JSON entry). Without it,
    `MUSE_DIR` ends up set but `mu2e` NOTFOUND and MU2E_SEARCH_PATH
    empty — `bin/json2jobdef` hard-exits in that state — so any caller
    driving json2jobdef must derive and pass this from the entry it is
    pushing, never guess a default.

    Credentials are NEVER remediated. A missing mu2epro token comes back
    as a non-zero rc with its stderr intact; no refresh is attempted,
    ever.
    """
    if run_as not in RUN_AS_VALUES:
        raise ValueError(
            f"run_as must be one of {RUN_AS_VALUES}, got {run_as!r}")
    _validate_entry_point(argv[0])
    if run_as == 'mu2epro':
        if cwd is not None:
            raise ValueError(
                "cwd has no effect under run_as='mu2epro': the ksu block "
                "always cd's into its own mktemp workdir. Pass cwd=None.")
        cmd = ksu_wrapper(argv, simjob_setup)
        run_cwd = REPO_ROOT
    else:
        cmd = _self_wrapper(argv, simjob_setup)
        run_cwd = cwd or REPO_ROOT
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=run_cwd)
    return {'rc': proc.returncode, 'stdout': proc.stdout,
            'stderr': proc.stderr}
