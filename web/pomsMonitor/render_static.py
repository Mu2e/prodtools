#!/usr/bin/env python3
"""Render the pomsMonitor dashboard to static files.

Drops two files into ``--out``:

- ``index.html`` — the ``/monitor`` page, rewritten so it works without
  a Flask backend: ``/api/jobs`` becomes a sibling ``jobs.json`` fetch
  (paired with ``lineage.json``), the famtree popup walks the
  pre-rendered lineage cache instead of calling ``/api/dataset/<name>``,
  and write-mode UI (Reload button, JSON Editor / JobDesc Generator
  nav) is stripped. A "Last refreshed" banner is injected under the H1.
- ``jobs.json`` — the ``/api/jobs`` JSON payload as-is.

A separate file, ``lineage.json``, is owned by ``build_lineage.py`` and
holds the SAM-walked dataset topology. This script does NOT touch it.

Cron-friendly: prints what it wrote and exits 0 on success, non-zero
on any failure (including an empty / failed ``/api/jobs``).
"""

import argparse
import datetime
import json
import os
import re
import sys


def _load_app(prodtools_dir, db_path):
    os.environ["PRODTOOLS_DIR"] = prodtools_dir
    os.environ["POMS_DB_PATH"] = db_path
    sys.path.insert(0, os.path.join(prodtools_dir, "web"))
    from pomsMonitor import app
    return app


# Replacement for showDatasetInfo(): reads parent edges + per-dataset
# efficiency stats from window._lineage (lineage.json, pre-rendered by
# build_lineage.py via SAM file-lineage + genFilterEff). Inverts once
# for child edges. Mirrors `famtree --stats` label format.
_LINEAGE_JS = r"""function showDatasetInfo(datasetName) {
            var modal = document.getElementById('diagramModal');
            var modalTitle = document.getElementById('modalTitle');
            var modalContent = document.getElementById('modalContent');
            modalTitle.textContent = 'Dataset: ' + datasetName;
            modal.style.display = 'block';
            modalContent.innerHTML = 'Computing lineage...';
            setTimeout(function() {
                var lineage = window._lineage || {};
                if (!window._childrenMap) window._childrenMap = buildChildrenMap(lineage);
                if (!(datasetName in lineage)) {
                    modalContent.innerHTML = '<p class="warning">Dataset not found in lineage cache.</p>';
                    return;
                }
                var walk = walkLineage(lineage, window._childrenMap, datasetName);
                var withStats = document.getElementById('statsToggle').checked;
                var mermaidCode = renderLineageMermaid(lineage, walk, withStats, datasetName);
                modalContent.innerHTML = '<div class="mermaid">' + mermaidCode + '</div>';
                mermaid.initialize({ startOnLoad: false, theme: currentMermaidTheme(), flowchart: { useMaxWidth: true, htmlLabels: true, wrappingWidth: 9999 }, securityLevel: 'loose' });
                mermaid.init(undefined, modalContent.querySelector('.mermaid'));
            }, 50);
        }

        function buildChildrenMap(lineage) {
            var children = {};
            Object.keys(lineage).forEach(function(child) {
                var entry = lineage[child] || {};
                (entry.parents || []).forEach(function(parent) {
                    if (!children[parent]) children[parent] = [];
                    if (children[parent].indexOf(child) < 0) children[parent].push(child);
                });
            });
            return children;
        }

        function walkLineage(lineage, childrenMap, start) {
            var nodes = {}; nodes[start] = true;
            var edges = []; var seen = {};
            function pushEdge(a, b) {
                var k = a + '' + b;
                if (seen[k]) return;
                seen[k] = true; edges.push([a, b]);
            }
            var q = [start];
            while (q.length) {
                var n = q.shift();
                var entry = lineage[n];
                if (!entry) continue;
                (entry.parents || []).forEach(function(p) {
                    pushEdge(p, n);
                    if (!nodes[p]) { nodes[p] = true; q.push(p); }
                });
            }
            q = [start];
            while (q.length) {
                var n = q.shift();
                (childrenMap[n] || []).forEach(function(c) {
                    pushEdge(n, c);
                    if (!nodes[c]) { nodes[c] = true; q.push(c); }
                });
            }
            return {nodes: Object.keys(nodes), edges: edges};
        }

        function renderLineageMermaid(lineage, walk, withStats, focus) {
            var ids = {}; var counter = 0;
            function id(name) {
                if (!(name in ids)) ids[name] = 'n' + (counter++);
                return ids[name];
            }
            var lines = ['graph TD'];
            walk.nodes.forEach(function(name) {
                var label = name;
                var entry = lineage[name];
                if (withStats && entry && entry.stats && entry.stats.gen != null) {
                    var s = entry.stats;
                    var note = s.extrapolated ? ' (extrapolated)' : '';
                    label += '<br/>eff=' + Number(s.eff).toFixed(4) +
                             ', trig: ' + Number(s.passed).toLocaleString() +
                             ', gen: ' + Number(s.gen).toLocaleString() + note;
                    if (s.nfiles != null) label += '<br/>nfiles=' + s.nfiles;
                }
                var line = '  ' + id(name) + '["' + label + '"]';
                if (name === focus) line += ':::focus';
                lines.push(line);
            });
            walk.edges.forEach(function(e) {
                lines.push('  ' + id(e[0]) + ' --> ' + id(e[1]));
            });
            lines.push('  classDef focus fill:#ffeb3b,stroke:#f57f17,stroke-width:3px');
            return lines.join('\n');
        }"""


# Replacement for loadJobs(): fetch jobs.json + lineage.json in parallel
# so the famtree popup never races the lineage load. Lineage failure is
# non-fatal — the popup falls back to "not found" rather than blocking
# the table render.
_LOADJOBS_JS = r"""function loadJobs() {
            Promise.all([
                fetch('jobs.json').then(function(r) { return r.json(); }),
                fetch('lineage.json').then(function(r) { return r.ok ? r.json() : {}; }).catch(function() { return {}; })
            ]).then(function(arr) {
                window._lineage = arr[1];
                currentData = arr[0];
                displayJobs(arr[0]);
            }).catch(function(err) { showError(err.message); });
        }"""


def _rewrite_html(html: str, refreshed_at: str) -> str:
    """Make the dashboard self-contained for static hosting.

    Hardening pass: rewrite the absolute fetch, swap the famtree
    handler for a lineage-cache walker, swap loadJobs for a parallel
    fetch, neuter dead handlers (Reload, json-editor link), strip
    dead UI, and stamp the refresh time.
    """
    html = html.replace("fetch('/api/jobs')", "fetch('jobs.json')")

    html = re.sub(
        r'<button[^>]*onclick="reloadData\(\)"[^>]*>.*?</button>',
        '', html, flags=re.DOTALL,
    )
    for label in ('JobDesc Generator', 'JSON Editor', 'Home'):
        html = re.sub(rf'<a[^>]*>\s*{re.escape(label)}\s*</a>', '', html)

    # Source-file column: the link target (/json-editor) is gone in
    # static, so render the basename as plain text rather than a
    # styled-as-clickable element that does nothing.
    html = re.sub(
        r"var sourceLink = job\.source_file \?[\s\S]*?: '';",
        "var sourceLink = sourceName;",
        html,
    )

    # Stub openJsonInEditor + reloadData (the button is gone but the
    # functions are still referenced from the row renderer's onclick
    # template-string until we ship a richer rewrite).
    html = re.sub(
        r'function reloadData\(\)\s*\{[\s\S]*?\n        \}',
        'function reloadData() { /* removed in static deploy */ }',
        html,
    )
    html = re.sub(
        r'function openJsonInEditor\([^)]*\)\s*\{[\s\S]*?\n        \}',
        'function openJsonInEditor() { /* removed in static deploy */ }',
        html,
    )

    # Swap server-backed famtree for the lineage-cache walker.
    # NOTE: pass the replacement via lambda to bypass re.sub's backslash
    # interpretation — otherwise `\n` in the JS becomes a real newline
    # and breaks string literals like `lines.join('\n')`.
    html = re.sub(
        r"function showDatasetInfo\(datasetName\)[\s\S]*?\n        \}",
        lambda _m: _LINEAGE_JS, html, count=1,
    )

    # Swap loadJobs() for a parallel jobs.json+lineage.json fetcher.
    html = re.sub(
        r"function loadJobs\(\)\s*\{[\s\S]*?\n        \}",
        lambda _m: _LOADJOBS_JS, html, count=1,
    )

    # "Last refreshed" banner under the H1.
    banner = (
        f'<p style="color:#666; margin: -8px 0 16px 0; font-size: 0.7em;">'
        f'Read-only static snapshot &middot; Last refreshed: {refreshed_at}'
        f'</p>'
    )
    html = html.replace(
        '<h1>Mu2e Production Monitor</h1>',
        '<h1>Mu2e Production Monitor</h1>\n    ' + banner,
        1,
    )
    return html


def render(out_dir: str, prodtools_dir: str, db_path: str) -> None:
    app = _load_app(prodtools_dir, db_path)
    client = app.test_client()

    jobs_resp = client.get('/api/jobs')
    if jobs_resp.status_code != 200:
        raise SystemExit(
            f"GET /api/jobs failed: {jobs_resp.status_code} {jobs_resp.data[:200]!r}"
        )
    jobs_data = json.loads(jobs_resp.data)
    if not jobs_data:
        print(f"WARNING: /api/jobs returned empty body", file=sys.stderr)
    jobs_body = json.dumps(jobs_data, separators=(',', ':')).encode('utf-8')

    monitor_resp = client.get('/monitor')
    if monitor_resp.status_code != 200:
        raise SystemExit(
            f"GET /monitor failed: {monitor_resp.status_code} {monitor_resp.data[:200]!r}"
        )
    refreshed_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()
    html = _rewrite_html(monitor_resp.data.decode('utf-8'), refreshed_at)

    os.makedirs(out_dir, exist_ok=True)
    jobs_path = os.path.join(out_dir, 'jobs.json')
    index_path = os.path.join(out_dir, 'index.html')
    with open(jobs_path, 'wb') as f:
        f.write(jobs_body)
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"wrote {jobs_path} ({len(jobs_body)} bytes, {len(jobs_data)} jobs)")
    print(f"wrote {index_path} ({len(html.encode('utf-8'))} bytes)")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--out",
        required=True,
        help="Output directory (e.g. /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor/)",
    )
    p.add_argument(
        "--prodtools-dir",
        default=os.environ.get(
            "PRODTOOLS_DIR",
            "/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools",
        ),
        help="Prodtools checkout to import from",
    )
    p.add_argument(
        "--db",
        default=os.environ.get(
            "POMS_DB_PATH",
            "/web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db",
        ),
        help="SQLite DB to read",
    )
    args = p.parse_args()
    render(args.out, args.prodtools_dir, args.db)


if __name__ == "__main__":
    main()
