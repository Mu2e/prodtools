#!/bin/bash

PRODTOOLS_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export PATH="$PRODTOOLS_DIR:$PATH"

PRODTOOLS_ROOT="$(dirname "$PRODTOOLS_DIR")"
export PYTHONPATH="$PRODTOOLS_ROOT:$PYTHONPATH"
