# OpenCode server image for Azure App Service (Web App for Containers).
#
# Config (opencode.json, AGENTS.md, plugins, commands) is baked read-only under
# XDG_CONFIG_HOME=/opt/xdg-config. Auth tokens + session data go to
# XDG_DATA_HOME=/home/.local/share, which lives on App Service's persisted
# /home mount so `opencode auth login` survives restarts. Working repos live in
# /home/workspace (also persisted).

FROM node:22-bookworm-slim

# Pin OpenCode. Bump deliberately.
ARG OPENCODE_VERSION=latest

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep python3 ca-certificates curl openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g opencode-ai@${OPENCODE_VERSION} \
    && npm cache clean --force

# Baked, read-only config tree.
ENV XDG_CONFIG_HOME=/opt/xdg-config
COPY opencode.json  /opt/xdg-config/opencode/opencode.json
COPY AGENTS.md      /opt/xdg-config/opencode/AGENTS.md
COPY rate-limit-fallback.json /opt/xdg-config/opencode/rate-limit-fallback.json
COPY plugin/        /opt/xdg-config/opencode/plugin/
COPY command/       /opt/xdg-config/opencode/command/

# Persisted data (auth.json, sessions) + workspace on App Service /home mount.
ENV XDG_DATA_HOME=/home/.local/share
ENV OPENCODE_WORKSPACE=/home/workspace

# App Service routes to this port; keep in sync with WEBSITES_PORT.
ENV PORT=8080
EXPOSE 8080

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
