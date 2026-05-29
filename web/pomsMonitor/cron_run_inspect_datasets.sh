#!/bin/bash
# Cron entry point for mu2epro's datasetMon nightly job.
#
# Original purpose: walk the production dataset lists and run
# inspect_datasets.py on each.
#
# Extended (2026-05) to also refresh the pomsMonitor static dashboard:
#   1. Rebuild SQLite from POMS map JSONs   (db_builder.py)
#   2. Refresh lineage topology + stats     (build_lineage.py, incremental)
#   3. Re-render index.html + jobs.json     (render_static.py)
#
# Source/destination paths:
#   prodtools deploy : /web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools/
#   SQLite DB        : /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db
#   static dashboard : /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor/
#
# Permission note: artifacts must be group-writable by `mu2e` (mu2epro's
# primary group). One-time setup if files are still oksuzian:nobody:
#   chgrp -R mu2e /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db \
#                 /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor
#   chmod -R g+w /web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db \
#                /web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor


# Initialize Mu2e, ops and ana environments
source ~/bin/authentication.sh
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup ops
source /cvmfs/mu2e.opensciencegrid.org/env/ana/current/bin/activate

# ---- 1. inspect_datasets (original payload) ----
for dataset_file in /exp/mu2e/app/users/mu2epro/production_manager/current_datasets/*/datasets_*.txt; do
    /exp/mu2e/app/home/mu2epro/cron/datasetMon/inspect_datasets.py --input-file "${dataset_file}"
done

# ---- 2. pomsMonitor refresh ----
PRODTOOLS_DIR=/web/sites/m/mu2e-exp.fnal.gov/cgi-bin/prodtools
POMS_DB=/web/sites/m/mu2e-exp.fnal.gov/data/poms_data.db
POMS_OUT=/web/sites/m/mu2e-exp.fnal.gov/htdocs/computing/ops/production/pomsMonitor

echo "=== $(date) pomsMonitor refresh: db_builder ==="
python3 "${PRODTOOLS_DIR}/utils/db_builder.py" --db "${POMS_DB}"

echo "=== $(date) pomsMonitor refresh: build_lineage (incremental) ==="
python3 "${PRODTOOLS_DIR}/web/pomsMonitor/build_lineage.py" \
    --db "${POMS_DB}" \
    --cache "${POMS_OUT}/lineage.json"

echo "=== $(date) pomsMonitor refresh: render_static ==="
python3 "${PRODTOOLS_DIR}/web/pomsMonitor/render_static.py" \
    --out "${POMS_OUT}" \
    --prodtools-dir "${PRODTOOLS_DIR}" \
    --db "${POMS_DB}"

echo "=== $(date) pomsMonitor refresh: done ==="
