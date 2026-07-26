"""Depth-bounded provenance traversal.

New code, not a wrapper: famtree.topology_for_dataset (famtree.py:118)
has no depth limit, no truncation signal, and walks parents only — its
recursion is a closure that cannot be parameterized. Nothing in famtree
walks children; samweb_wrapper.children_of_file (:417) is per-file.

Both edge functions are the fail-loud samweb_wrapper pair,
parents_of_file / children_of_file. famtree.get_parents is NOT used:
it delegates to file_lineage, which catches every exception and returns
[] (samweb_wrapper.py:248-260). An expired token would then render as
"this file has no parents" — "it is a primary" — a materially wrong
answer for a lineage tool, and lru_cache would keep serving it after SAM
recovered. lru_cache does not memoize exceptions, so a raising edge
function also cannot poison the cache.

This module keeps bounded caches (module-level singletons). famtree's
own lru_cache(maxsize=None) (famtree.py:46) is likewise avoided: it
grows without limit in a long-lived server. Lineage is immutable so
cached values stay correct.
"""
import functools

from prodtools_mcp.adapters import ToolError

DIRECTIONS = ('up', 'down')
MAX_DEPTH = 10
_CACHE_SIZE = 4096


def walk(root, direction, depth, edge_fn):
    """Breadth-first walk to `depth` levels.

    Returns (nodes, edges, truncated). `truncated` is True when the depth
    limit cut the walk short — the caller must be able to tell a complete
    answer from a clipped one.
    """
    seen = {root}
    order = [root]
    frontier = [root]
    edges = []
    for _ in range(depth):
        nxt = []
        for node in frontier:
            for other in edge_fn(node):
                if direction == 'up':
                    edge = {'child': node, 'parent': other}
                else:
                    edge = {'child': other, 'parent': node}
                if edge not in edges:
                    edges.append(edge)
                if other not in seen:
                    seen.add(other)
                    order.append(other)
                    nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return order, edges, bool(frontier)


@functools.lru_cache(maxsize=_CACHE_SIZE)
def _cached_parents(name):
    # parents_of_file, not famtree.get_parents: see the module docstring.
    # It applies the same etc.*.txt filter but raises on SAM errors
    # instead of returning an empty parent list.
    from utils.samweb_wrapper import parents_of_file
    return tuple(parents_of_file(name))


@functools.lru_cache(maxsize=_CACHE_SIZE)
def _cached_children(name):
    from utils.samweb_wrapper import children_of_file
    return tuple(children_of_file(name))


def _default_parents_fn():
    return _cached_parents


def _default_children_fn():
    return _cached_children


def trace_provenance(name, direction='up', depth=3,
                     parents_fn=None, children_fn=None):
    """Lineage of a file as nodes and edges.

    Returns data, not presentation — no mermaid string; the caller can
    render one from the edges.
    """
    if direction not in DIRECTIONS:
        raise ToolError('invalid_argument',
                        f'unknown direction {direction!r}',
                        f'Expected one of {DIRECTIONS}.')
    if not isinstance(depth, int) or depth < 1 or depth > MAX_DEPTH:
        raise ToolError('invalid_argument',
                        f'depth must be an integer in 1..{MAX_DEPTH}, '
                        f'got {depth!r}',
                        f'Use a depth between 1 and {MAX_DEPTH}.')

    if direction == 'up':
        edge_fn = parents_fn or _default_parents_fn()
    else:
        edge_fn = children_fn or _default_children_fn()

    try:
        nodes, edges, truncated = walk(name, direction, depth, edge_fn)
    except Exception as exc:
        raise ToolError(
            'catalog_unavailable',
            f'lineage lookup failed for {name}: {exc}',
            'Check SAM availability and that muse setup ops has run.'
        ) from exc

    return {'root': name, 'direction': direction, 'depth': depth,
            'truncated': truncated, 'nodes': nodes, 'edges': edges}
