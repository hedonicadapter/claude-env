#!/usr/bin/env bash
# Start the OpenCode web server for Azure App Service.
set -euo pipefail

PORT="${PORT:-8080}"
WORKSPACE="${OPENCODE_WORKSPACE:-/home/workspace}"

# Persisted dirs on the /home mount.
mkdir -p "$WORKSPACE" "${XDG_DATA_HOME:-/home/.local/share}/opencode"

cd "$WORKSPACE"

# First-run hint: auth.json absent means `opencode auth login` hasn't run yet.
# Do it once via the App Service SSH console (see DEPLOY-AZURE.md).
if [ ! -f "${XDG_DATA_HOME:-/home/.local/share}/opencode/auth.json" ]; then
  echo "WARN: no auth.json yet — run 'opencode auth login' via SSH console (GitHub Copilot)." >&2
fi

# OPENCODE_SERVER_PASSWORD (optional) is defense-in-depth behind Easy Auth.
# Bind all interfaces so the App Service front end can reach the container.
exec opencode web --hostname 0.0.0.0 --port "$PORT"
