#!/bin/bash
echo "🔧 Setting up prodtools environment..."

# Add prodtools bin directory to PATH
PRODTOOLS_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
export PATH="$PRODTOOLS_DIR:$PATH"

# Add prodtools to PYTHONPATH for imports
PRODTOOLS_ROOT="$(dirname "$PRODTOOLS_DIR")"
export PYTHONPATH="$PRODTOOLS_ROOT:$PYTHONPATH"

echo "✅ prodtools environment ready!"
echo "   - prodtools commands available: json2jobdef, fcldump, runjobdef, etc."
