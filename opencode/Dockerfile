# OpenCode server image for Azure App Service (Web App for Containers).
#
# Config (opencode.json, AGENTS.md, plugins, commands) is baked read-only under
# XDG_CONFIG_HOME=/opt/xdg-config. Auth tokens + session data go to
# XDG_DATA_HOME=/home/.local/share, which lives on App Service's persisted
# /home mount so `opencode auth login` survives restarts. Working repos live in
# /home/workspace (also persisted).

# --- Stage 1: build the rtk binary from git (NOT crates.io — name collision) ---
# Pinned to v0.41.0, the rev claude-env verifies against `rtk gain`. --locked
# pins the dependency tree (build.rs runs at install time).
FROM rust:1-bookworm AS rtk
RUN cargo install --git https://github.com/rtk-ai/rtk \
      --rev 4c6d9147c46384e61652f4cb6c8f0c695f017bfc --locked \
      --root /out

# --- Stage 2: runtime ---
FROM node:22-bookworm-slim

# Pin OpenCode. Bump deliberately.
ARG OPENCODE_VERSION=latest

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep python3 ca-certificates curl openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g opencode-ai@${OPENCODE_VERSION} \
    && npm cache clean --force

# rtk on PATH (used by the generated OpenCode plugin's tool.execute.before hook).
COPY --from=rtk /out/bin/rtk /usr/local/bin/rtk

# Baked, read-only config tree.
ENV XDG_CONFIG_HOME=/opt/xdg-config
COPY opencode.json  /opt/xdg-config/opencode/opencode.json
COPY AGENTS.md      /opt/xdg-config/opencode/AGENTS.md
COPY rate-limit-fallback.json /opt/xdg-config/opencode/rate-limit-fallback.json
COPY plugins/       /opt/xdg-config/opencode/plugins/
COPY command/       /opt/xdg-config/opencode/command/

# Generate the official rtk OpenCode plugin into the baked config
# (~/.config/opencode/plugins/rtk.ts, resolved via XDG_CONFIG_HOME). Coexists
# with plugins/notify.ts. `rtk gain` proves it's the right binary, not Rust Type Kit.
RUN rtk gain >/dev/null 2>&1 && rtk init -g --opencode \
    && test -f /opt/xdg-config/opencode/plugins/rtk.ts

# Persisted data (auth.json, sessions) + workspace on App Service /home mount.
ENV XDG_DATA_HOME=/home/.local/share
ENV OPENCODE_WORKSPACE=/home/workspace

# App Service routes to this port; keep in sync with WEBSITES_PORT.
ENV PORT=8080
EXPOSE 8080

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
