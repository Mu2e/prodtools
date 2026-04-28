#!/bin/bash
# Lightweight helper to prepare environment for Run1B CeEndpoint digitization
# Usage: source bin/setup_run1b.sh

# Source the project-wide setup (if present)
if [ -f "$(dirname "$BASH_SOURCE")/setup.sh" ]; then
  source "$(dirname "$BASH_SOURCE")/setup.sh"
fi

# Ensure local directory is in MU2E_SEARCH_PATH so fcl/ files are discoverable
export MU2E_SEARCH_PATH=".:$MU2E_SEARCH_PATH"

# Echo what we did for convenience
echo "Sourced setup.sh (if present) and set MU2E_SEARCH_PATH to include ."
