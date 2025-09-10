#!/bin/bash
echo "🔧 Setting up prodtools environment..."

# Add prodtools and test directories to PATH
PRODTOOLS_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export PATH="$PRODTOOLS_DIR:$PATH"
