"""Identity dispatch for the write server.

The ONLY place that knows how to become mu2epro, and the ONLY place
that enforces confirm. Kept apart from tools.py so the security-
critical logic is testable without MCP plumbing.
"""
import os
import re
import subprocess

RUN_AS_VALUES = ('self', 'mu2epro')

# --- the exit status of anything run through `ksu -e` is UNUSABLE ------
#
# MIT ksu (krb5 1.21.1, this host) does NOT propagate the child's exit
# code on its non-`execv` path: it waits, then calls
# `exit(raw_wait_status)`. A normal exit N encodes as N<<8, and exit()
# keeps only the low 8 bits, so EVERY ordinary child status truncates to
# 0. Measured here: children exiting 1, 2, 7, 42 and 255 all give
# `ksu` rc=0. (Only a signal death survives -- a SIGKILLed child gives
# 9 -- because the signal number lives in the low byte. A bare
# `bash -c 'exit 7'` does give 7; the loss is specifically ksu's.)
#
# Every write tool reads that rc to decide success, so before this
# sentinel existed a crashed tick, a failed push, and a refused
# duplicate campaign all reported SUCCESS -- silently, and in
# production. NOTE: restoring the `RC=${PIPESTATUS[0]}; exit $RC` tail
# from the .claude/commands/mu2epro-submit.md block does NOT help.
# ksu truncates whatever the inner shell exits with, so no amount of
# care INSIDE the script can get a status out through ksu's own rc.
# Do not "simplify" this back to trusting `proc.returncode`.
#
# The status therefore travels out of band, on stderr, as
# `__PRODTOOLS_RC__:<n>` -- text ksu passes through untouched.
RC_SENTINEL_PREFIX = '__PRODTOOLS_RC__:'

_RC_SENTINEL_RE = re.compile(
    r'^' + re.escape(RC_SENTINEL_PREFIX) + r'(\d+)\s*$')

# Reported when stderr carries NO sentinel: the child's real status is
# then unknown, and unknown is treated as failure, never as success.
# Deliberately not 0, 1 or 2: `submissions run` documents 2 as "ran
# fine, something needs a human", and an unknown status must never
# masquerade as that.
SENTINEL_MISSING_RC = 125

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
# `setup OfflineOps` (not present in that source block) and replaces its
# `RC=${PIPESTATUS[0]}; exit $RC` tail with the stderr sentinel (see
# RC_SENTINEL_PREFIX: that tail cannot work through ksu). Every exported
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
#
# The whole setup-plus-command sequence runs inside a SUBSHELL, and the
# sentinel is echoed from outside it. That shape is what makes the
# sentinel unconditional: the `exit 1` guards above end the SUBSHELL,
# not the script, so a failed CVMFS source still reaches the echo
# below. Written as a plain `&&` chain the first guard would exit the
# whole `bash -c` before any sentinel could be printed, and run_cli
# would see an rc-less failure — the very hole this closes.
#
# The sentinel is emitted with a LEADING newline, via printf rather than
# echo. `echo` appends to whatever stderr byte came last, so a command
# whose final stderr write lacks a trailing newline produces
# `partial line__PRODTOOLS_RC__:0` — which the anchored regex misses, so
# run_cli reports 125 on a SUCCESSFUL run. The leading newline puts the
# sentinel at the start of its own line unconditionally. The cost is one
# blank line in captured stderr on the (usual) well-terminated path.
_RC_TAIL = f"""__prodtools_rc=$?
printf '\\n{RC_SENTINEL_PREFIX}%s\\n' "$__prodtools_rc" >&2
exit $__prodtools_rc
"""

_SETUP_CHAIN = """(
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1 \\
  || {{ echo 'push_cnf: setupmu2e-art.sh failed' >&2; exit 1; }} \\
  && muse setup ops > /dev/null 2>&1 \\
  || {{ echo 'push_cnf: muse setup ops failed' >&2; exit 1; }} \\
  && setup OfflineOps > /dev/null 2>&1 \\
  || {{ echo 'push_cnf: setup OfflineOps failed' >&2; exit 1; }} \\
  && {musing_clause}{command}
)
""" + _RC_TAIL

# `mktemp ... || exit 1` stays a hard exit: it is BEFORE the setup
# subshell, so it prints no sentinel — and a sentinel-less result is
# reported as a failure by run_cli anyway (fail-closed), which is the
# right answer for "could not even make a workdir".
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


def _rc_from_sentinel(proc_rc, stderr):
    """(rc, stderr-without-sentinel-lines) for a finished process.

    The LAST sentinel wins: ours is echoed after the command has
    finished, so anything a child printed that happens to look like one
    is necessarily earlier. Every sentinel-shaped line is stripped from
    the returned stderr, spoofed ones included, so callers only ever see
    the command's own output.

    No sentinel at all means the shell died before the echo could run
    (or something ate stderr) — the child's status is UNKNOWN, and this
    returns a failure rc rather than the process rc of 0 that ksu
    manufactures. A nonzero process rc is still trustworthy in that case
    and is passed through (it can only be ksu's own failure or a signal
    death, never a truncated child status), EXCEPT for 2: that is
    `submissions run`'s "ran fine, needs a human" code, which an unknown
    status must never be reported as.
    """
    kept, rc = [], None
    for line in stderr.splitlines(keepends=True):
        m = _RC_SENTINEL_RE.match(line.rstrip('\n'))
        if m:
            rc = int(m.group(1))
            continue
        kept.append(line)
    if rc is not None:
        return rc, ''.join(kept)
    unknown = (proc_rc if proc_rc not in (0, 2) else SENTINEL_MISSING_RC)
    note = (f"run_cli: no {RC_SENTINEL_PREFIX} status sentinel on stderr "
            f"(process rc={proc_rc}) — the command's real exit status is "
            f"unknown, reporting rc={unknown} (failure)\n")
    return unknown, ''.join(kept) + note


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

    The returned `rc` is the STDERR SENTINEL's value, not
    `proc.returncode` — `ksu -e` truncates every ordinary child status
    to 0 (see RC_SENTINEL_PREFIX). Both identities go through the same
    parse: run_as='self' is not subject to the truncation, but a second
    code path that trusted the process rc there would be one refactor
    away from being reused for mu2epro, and an unchecked rc is the
    failure mode this whole mechanism exists to prevent.
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
    rc, stderr = _rc_from_sentinel(proc.returncode, proc.stderr)
    return {'rc': rc, 'stdout': proc.stdout, 'stderr': stderr}
