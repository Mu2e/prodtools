#!/bin/bash
# install_prodtools.sh - Install a prodtools release on CVMFS
# Run directly on cvmfsmu2e@oasiscfs.fnal.gov
# Usage: ./install_prodtools.sh [-n] [-t [DIR]] v1.6.3
#   -n          Dry run: check GitHub tag and install path, but make no changes.
#   -t [DIR]    Test mode: skip cvmfs_server calls and install into a local
#               writable dir (default: a fresh mktemp -d). Lets you exercise
#               the fetch/extract/mv/symlink logic without touching CVMFS.

set -e

DRY_RUN=false
TEST_MODE=false
TEST_BASE=""
while [[ "$1" == -* ]]; do
  case "$1" in
    -n) DRY_RUN=true; shift ;;
    -t)
      TEST_MODE=true
      shift
      # Optional path argument for -t. Anything starting with '-' or nothing
      # left = default to mktemp.
      if [[ -n "$1" && "$1" != -* && ! "$1" =~ ^v?[0-9]+\.[0-9]+ ]]; then
        TEST_BASE="$1"
        shift
      fi
      ;;
    *) break ;;
  esac
done

VER=${1:?Usage: $0 [-n] [-t [DIR]] <version> (e.g. v1.6.3)}
CVMFS_REPO=mu2e.opensciencegrid.org
if $TEST_MODE; then
  INSTALL_BASE=${TEST_BASE:-$(mktemp -d /tmp/test_cvmfs_install.XXXXXX)}
  mkdir -p "${INSTALL_BASE}"
  echo "=== TEST MODE: install base is ${INSTALL_BASE} (no cvmfs_server calls) ==="
else
  INSTALL_BASE=/cvmfs/${CVMFS_REPO}/bin/prodtools
fi
TMPDIR=$(mktemp -d)

cleanup() {
  rm -rf "${TMPDIR}"
}
trap cleanup EXIT

abort_transaction() {
  if $TEST_MODE; then
    echo "ERROR: aborting (test mode — no cvmfs_server abort)"
    return
  fi
  echo "ERROR: Aborting CVMFS transaction..."
  cd ~
  cvmfs_server abort -f ${CVMFS_REPO} 2>/dev/null || true
  echo "WARNING: Per CVMFS policy, run an empty transaction+publish to restore correct permissions:"
  echo "  cvmfs_server transaction ${CVMFS_REPO} && cvmfs_server publish ${CVMFS_REPO}"
}
trap abort_transaction ERR

# Verify the GitHub release tag exists.
# -L is required: github.com archive URLs 302-redirect to S3, and without
# -L a bare HEAD returns the redirect as a successful 3xx even when the
# tag doesn't exist (false positive). With -L we follow through to the
# real resource and fail cleanly on 404.
echo "Checking GitHub release ${VER}..."
curl -fsIL "https://github.com/Mu2e/prodtools/archive/refs/tags/${VER}.tar.gz" > /dev/null \
  || { echo "ERROR: Release ${VER} not found on GitHub."; exit 1; }
echo "Release ${VER} found."

# Check not already installed
if [ -d "${INSTALL_BASE}/${VER}" ]; then
  echo "ERROR: ${INSTALL_BASE}/${VER} already exists. Aborting."
  exit 1
fi

if $DRY_RUN; then
  echo "[dry-run] Would install to ${INSTALL_BASE}/${VER}"
  echo "[dry-run] Would update ${INSTALL_BASE}/current -> ${VER}"
  echo "[dry-run] No changes made."
  exit 0
fi

if $TEST_MODE; then
  echo "Skipping cvmfs_server transaction (test mode)"
else
  echo "Opening CVMFS transaction..."
  cvmfs_server transaction ${CVMFS_REPO}
fi

echo "Downloading and extracting prodtools ${VER}..."
curl -fsSL "https://github.com/Mu2e/prodtools/archive/refs/tags/${VER}.tar.gz" \
  | tar -xz -C ${TMPDIR}

# GitHub strips the leading 'v' from the directory name
SRC_DIR=$(ls -d ${TMPDIR}/prodtools-*/)
mv "${SRC_DIR}" "${INSTALL_BASE}/${VER}"

echo "Updating 'current' symlink to ${VER}..."
ln -sfn "${VER}" "${INSTALL_BASE}/current"

if $TEST_MODE; then
  echo "Skipping cvmfs_server publish (test mode)"
else
  echo "Publishing to CVMFS..."
  cd ~
  cvmfs_server publish ${CVMFS_REPO}
fi

echo "Done. prodtools ${VER} is now available at ${INSTALL_BASE}/${VER}"
echo "       'current' symlink points to ${VER}"
