"""Depth-bounded provenance traversal.

New code, not a wrapper: famtree.topology_for_dataset (famtree.py:118)
has no depth limit, no truncation signal, and walks parents only — its
recursion is a closure that cannot be parameterized. Nothing in famtree
walks children; samweb_wrapper.children_of_file (:417) is per-file.

famtree.get_parents is lru_cache(maxsize=None) (famtree.py:46). Lineage
is immutable so its cached values stay correct, but an unbounded cache
in a long-lived server grows without limit, so this module keeps its own
bounded one.
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


def _default_parents_fn():
    from utils.famtree import get_parents

    @functools.lru_cache(maxsize=_CACHE_SIZE)
    def parents(name):
        return tuple(get_parents(name))
    return parents


def _default_children_fn():
    from utils.samweb_wrapper import children_of_file

    @functools.lru_cache(maxsize=_CACHE_SIZE)
    def children(name):
        return tuple(children_of_file(name))
    return children


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
