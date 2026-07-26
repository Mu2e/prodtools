"""Boundary layer between MCP tool calls and prodtools internals.

Three jobs, all load-bearing for a stdio server:

1. Trap SystemExit. It derives from BaseException, so `except Exception`
   misses it; uncaught it terminates the server rather than failing one
   call. Reachable examples on these tools' paths: submissions.resolve_cap
   (utils/submissions.py:59) and _acquire_lock (utils/submissions.py:648).
2. Guard stdout. In a stdio server stdout IS the JSON-RPC channel, and
   utils/famtree.py:71 prints to it on the not-found path, directly on
   trace_provenance's route. Stray output is rerouted to stderr.
3. Build the error envelope every tool returns on failure.
"""
import contextlib
import functools
import sys

ERROR_KINDS = (
    'env_missing',
    'auth_expired',
    'catalog_unavailable',
    'not_found',
    'invalid_argument',
    'internal',
)


class ToolError(Exception):
    """A failure a tool can describe precisely. Carries a closed-set kind
    so callers can branch without parsing prose."""

    def __init__(self, kind, message, remedy=''):
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown error kind {kind!r}; "
                             f"expected one of {ERROR_KINDS}")
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.remedy = remedy


ENV_REMEDY = ('Run `muse setup ops` in the shell that starts this '
              'server, then restart it.')
AUTH_REMEDY = ('Renew your credentials in your own shell (htgettoken). '
               'This server never refreshes credentials — do not retry '
               'until you have.')
CATALOG_REMEDY = 'Check SAM availability and that muse setup ops has run.'

# Substrings that identify an authorization failure in a SAM/HTTP error.
# Deliberately narrow: a broader net would reclassify ordinary outages as
# auth_expired and send the caller to renew a perfectly good token.
_AUTH_MARKERS = ('401', '403', 'token', 'unauthorized')


def classify_catalog_error(exc, message):
    """Build the right ToolError for a failure on a catalog code path.

    Without this, `auth_expired` and `env_missing` are declared in
    ERROR_KINDS and produced nowhere: every catalog failure came back as
    catalog_unavailable with "Check SAM availability", so an expired
    token sent the caller to check a service that was fine. The server's
    own guidance says "never retry an auth_expired" — a branch that
    could not fire.
    """
    if isinstance(exc, ImportError):
        return ToolError('env_missing', message, ENV_REMEDY)
    text = str(exc).lower()
    if any(marker in text for marker in _AUTH_MARKERS):
        return ToolError('auth_expired', message, AUTH_REMEDY)
    return ToolError('catalog_unavailable', message, CATALOG_REMEDY)


def error(kind, message, remedy=''):
    """Build the error envelope. Validates `kind` against the closed set
    so a typo becomes a loud failure here rather than a silently
    unhandleable payload at the caller."""
    if kind not in ERROR_KINDS:
        raise ValueError(f"unknown error kind {kind!r}; "
                         f"expected one of {ERROR_KINDS}")
    return {'error': {'kind': kind, 'message': message, 'remedy': remedy}}


def safe_tool(fn):
    """Wrap a tool function: stdout guarded, exceptions enveloped.

    SystemExit is caught explicitly and BEFORE the general handler —
    `except Exception` would not match it.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            with contextlib.redirect_stdout(sys.stderr):
                return fn(*args, **kwargs)
        except ToolError as exc:
            return error(exc.kind, exc.message, exc.remedy)
        except SystemExit as exc:
            return error(
                'internal',
                f'{fn.__name__} exited: {exc}',
                'This is a prodtools bug — a util called sys.exit() on a '
                'server code path. Report the tool name and arguments.')
        except Exception as exc:
            return error(
                'internal',
                f'{fn.__name__} failed: {type(exc).__name__}: {exc}',
                'Unexpected failure; check the server stderr log.')
    return wrapper
