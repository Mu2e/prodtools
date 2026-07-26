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
