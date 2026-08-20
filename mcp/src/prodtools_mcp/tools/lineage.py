"""Depth-bounded provenance traversal.

New code, not a wrapper: famtree's own dataset-topology walker had no
depth limit, no truncation signal, and walked parents only — its
recursion was a closure that could not be parameterized. Since nothing
ever called it, it was removed 2026-08-20 and this is the only
dataset-level traversal left. Nothing in famtree walks children;
samweb_wrapper.children_of_file (:436) is per-file.

Both edge functions are the fail-loud samweb_wrapper pair,
parents_of_file / children_of_file. famtree.get_parents is NOT used:
it delegates to file_lineage, which catches every exception and returns
[] (samweb_wrapper.py:260-265). An expired token would then render as
"this file has no parents" — "it is a primary" — a materially wrong
answer for a lineage tool, and lru_cache would keep serving it after SAM
recovered. lru_cache does not memoize exceptions, so a raising edge
function also cannot poison the cache.

This module keeps bounded caches (module-level singletons). famtree's
own lru_cache(maxsize=None) (famtree.py:46) is likewise avoided: it
grows without limit in a long-lived server. Lineage is immutable so
cached values stay correct.

`depth` alone does not bound the query cost: each level multiplies —
a mixed `dig` file has ~33 parents, so a level-2 frontier is already
~1,000 nodes, and one parents_of_file call costs ~0.5s. The default
depth=3 could therefore fan out to thousands of serial SAM queries.
`max_nodes` (mirroring find_datasets' `limit`) caps the walk on the
number of nodes it is willing to discover, and — the part that
actually matters — the check runs BEFORE calling edge_fn on the next
node, so a budget that is already spent stops new queries rather than
merely trimming the answer after the fact.
"""
import functools

from prodtools_mcp.adapters import ToolError, classify_catalog_error

DIRECTIONS = ('up', 'down')
MAX_DEPTH = 10
MAX_NODES = 2000
DEFAULT_MAX_NODES = 500
_CACHE_SIZE = 4096


def walk(root, direction, depth, edge_fn, max_nodes=DEFAULT_MAX_NODES):
    """Breadth-first walk to `depth` levels, or `max_nodes` discovered
    nodes, whichever comes first.

    Returns (nodes, edges, truncated). `truncated` is True when either
    the depth limit or the node budget cut the walk short — the caller
    must be able to tell a complete answer from a clipped one.

    The `max_nodes` check happens at the top of the per-node loop, before
    `edge_fn` is called for that node. That ordering is the point: once
    the budget is spent, no further SAM queries are issued for the
    remainder of the frontier (or the walk). `nodes` is still trimmed to
    `max_nodes` on return so the size guarantee holds even though one
    already-issued edge_fn call can return more results than are left in
    the budget (a single mixed dig file can hand back ~33 parents at
    once).
    """
    seen = {root}
    order = [root]
    frontier = [root]
    edges = []
    budget_exceeded = False
    for _ in range(depth):
        nxt = []
        for node in frontier:
            if len(order) >= max_nodes:
                budget_exceeded = True
                break
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
        if budget_exceeded:
            break
        frontier = nxt
        if not frontier:
            break
    truncated = bool(frontier) or budget_exceeded or len(order) > max_nodes
    if len(order) > max_nodes:
        kept = set(order[:max_nodes])
        order = order[:max_nodes]
        edges = [e for e in edges if e['child'] in kept and e['parent'] in kept]
    return order, edges, truncated


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
                     max_nodes=DEFAULT_MAX_NODES,
                     parents_fn=None, children_fn=None):
    """Lineage of a file as nodes and edges.

    Returns data, not presentation — no mermaid string; the caller can
    render one from the edges.

    `max_nodes` bounds the query fan-out the same way `depth` bounds the
    hop count: a mixed dig file has ~33 parents, so depth alone can walk
    into thousands of serial SAM queries at the default depth=3. See
    `walk()` for how the budget is enforced.
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
    if (not isinstance(max_nodes, int) or isinstance(max_nodes, bool)
            or max_nodes < 1 or max_nodes > MAX_NODES):
        raise ToolError('invalid_argument',
                        f'max_nodes must be an integer in 1..{MAX_NODES}, '
                        f'got {max_nodes!r}',
                        f'Use a max_nodes between 1 and {MAX_NODES}.')

    if direction == 'up':
        edge_fn = parents_fn or _default_parents_fn()
    else:
        edge_fn = children_fn or _default_children_fn()

    try:
        nodes, edges, truncated = walk(name, direction, depth, edge_fn,
                                       max_nodes)
    except Exception as exc:
        raise classify_catalog_error(
            exc, f'lineage lookup failed for {name}: {exc}') from exc

    return {'root': name, 'direction': direction, 'depth': depth,
            'max_nodes': max_nodes, 'truncated': truncated,
            'nodes': nodes, 'edges': edges}
