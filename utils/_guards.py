"""Runtime guards for optional Python dependencies.

The default `muse setup ops` spack env (ops-019) does NOT include
scientific Python packages like SQLAlchemy or Flask. Modules that need
those expect the user to run `pyenv ana` after `muse setup ops` — see
memory `reference_pyenv_ana_for_db.md`.

Use `require_packages('sqlalchemy', ...)` early in any bin/ entry point
that depends on those modules; on missing imports it prints a clear,
actionable one-line message and exits 2.
"""
import importlib
import sys


_PRETTY_NAMES = {
    'sqlalchemy': 'SQLAlchemy',
    'flask': 'Flask',
}


def require_packages(*module_names):
    """Exit (status 2) if any of the named modules cannot be imported.
    Prints a single-line stderr message naming the missing packages and
    pointing at the canonical fix (`pyenv ana`)."""
    missing = []
    for name in module_names:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if not missing:
        return
    pretty = ' and '.join(_PRETTY_NAMES.get(n, n) for n in missing)
    sys.stderr.write(
        f"error: {pretty} not found. Run 'pyenv ana' after 'muse setup ops'.\n"
    )
    sys.exit(2)
