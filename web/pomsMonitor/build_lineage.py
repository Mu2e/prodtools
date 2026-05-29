#!/usr/bin/env python3
"""Build/refresh the lineage topology + stats cache for the static dashboard.

Two passes per run, both incremental:

1. **Topology** — for any output dataset in the prodtools SQLite DB
   not yet in ``lineage.json``, call ``famtree.topology_for_dataset``
   to walk SAM file-lineage and seed the dataset (and all its
   ancestors) with a parents list.
2. **Stats** — for any cache entry whose ``stats`` is still null,
   call ``famtree.get_dataset_efficiency`` (samples 10 files via SAM
   metadata, extrapolates) and cache the result. Empty dict means
   "tried, no efficiency data" (reco/ntuple stages without
   ``dh.gencount``) and won't be retried.

Cache shape: ``{dataset: {"parents": [...], "stats": {...} | {} | null}}``
keyed by 5-field dataset name. The static dashboard ships this so the
famtree popup is a pure client-side lookup.

Old shape (``{dataset: [parents]}``, no stats) is auto-migrated on load.
"""

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "utils"))

from utils.famtree import topology_for_dataset, get_dataset_efficiency
from samweb_wrapper import get_samweb_wrapper


def _unique_outputs(db_path):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT dataset FROM job_outputs WHERE dataset IS NOT NULL").fetchall()
    return sorted(r[0] for r in rows)


def _load_cache(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    # Migrate old shape (list of parents) to new shape.
    out = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[k] = {"parents": v, "stats": None}
        else:
            out[k] = v
    return out


def _flush(cache, path):
    with open(path, 'w') as f:
        json.dump(cache, f, separators=(',', ':'))


def _walk_topology(cache, todo, cache_path):
    for i, ds in enumerate(todo, 1):
        t0 = time.time()
        try:
            topo = topology_for_dataset(ds)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] ERROR topo {ds}: {e}", flush=True)
            cache[ds] = {"parents": [], "stats": None}
            continue
        elapsed = time.time() - t0
        n = 0
        if topo:
            for k, parents in topo.items():
                if k not in cache:
                    cache[k] = {"parents": parents, "stats": None}
                    n += 1
        cache.setdefault(ds, {"parents": [], "stats": None})
        print(f"  [{i}/{len(todo)}] {ds}  (+{n} new datasets, {elapsed:.1f}s)", flush=True)
        if i % 25 == 0:
            _flush(cache, cache_path)


def _fetch_stats(cache, needs_stats, cache_path):
    samweb = get_samweb_wrapper()
    for i, ds in enumerate(needs_stats, 1):
        t0 = time.time()
        try:
            stats = get_dataset_efficiency(ds, samweb)
        except Exception as e:
            print(f"  [{i}/{len(needs_stats)}] ERROR stats {ds}: {e}", flush=True)
            cache[ds]["stats"] = {}
            continue
        elapsed = time.time() - t0
        if stats:
            passed, gen, eff, nfiles, extrap = stats
            cache[ds]["stats"] = {
                "eff": eff, "passed": passed, "gen": gen,
                "nfiles": nfiles, "extrapolated": extrap,
            }
            print(f"  [{i}/{len(needs_stats)}] {ds}  eff={eff:.4f} ({elapsed:.1f}s)", flush=True)
        else:
            cache[ds]["stats"] = {}
            print(f"  [{i}/{len(needs_stats)}] {ds}  (no stats, {elapsed:.1f}s)", flush=True)
        if i % 25 == 0:
            _flush(cache, cache_path)


def build(db_path, cache_path, limit=None, force=False, skip_stats=False):
    cache = {} if force else _load_cache(cache_path)
    datasets = _unique_outputs(db_path)
    todo = [ds for ds in datasets if ds not in cache]
    if limit:
        todo = todo[:limit]
    print(f"== Topology ==", flush=True)
    print(f"Cache has {len(cache)} datasets; {len(todo)} new to walk "
          f"({len(datasets)} total in DB).", flush=True)
    _walk_topology(cache, todo, cache_path)

    if not skip_stats:
        needs_stats = [k for k, v in cache.items() if v.get("stats") is None]
        if limit:
            needs_stats = needs_stats[:limit]
        print(f"\n== Stats ==", flush=True)
        print(f"{len(needs_stats)} datasets need efficiency stats.", flush=True)
        _fetch_stats(cache, needs_stats, cache_path)

    _flush(cache, cache_path)
    print(f"wrote {cache_path} ({len(cache)} datasets)")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True, help="SQLite DB to read")
    p.add_argument("--cache", required=True, help="lineage.json path")
    p.add_argument("--limit", type=int, default=None, help="cap entries per phase (testing)")
    p.add_argument("--force", action="store_true", help="ignore existing cache, re-render all")
    p.add_argument("--skip-stats", action="store_true", help="topology only; skip efficiency stats")
    args = p.parse_args()
    build(args.db, args.cache, limit=args.limit, force=args.force, skip_stats=args.skip_stats)


if __name__ == "__main__":
    main()
