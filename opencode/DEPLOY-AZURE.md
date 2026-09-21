# Deploy OpenCode to Azure App Service with Entra ID auth

This runbook stands up your `claude-env` OpenCode port as a browser chat UI at
`https://<app>.azurewebsites.net`, gated by your Microsoft/Entra login (Azure
"Easy Auth"), running on your monthly Azure credits.

## Architecture and the two auth layers

```
Browser ──HTTPS──▶ App Service front end
                     │  Layer A: Easy Auth (Entra ID)  ── who may open the UI
                     ▼
                   Container: `opencode web` on :8080
                     │  Layer B: opencode auth  ── which LLM account pays
                     ├─▶ GitHub Copilot (device OAuth)   primary → Claude + GPT
                     └─▶ Anthropic Pro/Max (optional)    switchable fallback
```

- **Layer A** is Azure-native; no separate auth service. Unauthenticated
  requests are redirected to Entra sign-in.
- **Layer B** is OpenCode's own login, done once inside the container. Tokens
  land in `/home/.local/share/opencode/auth.json`, which is on the persisted
  `/home` mount, so they survive restarts and redeploys.

## Prerequisites

- Azure CLI (`az login`) with rights to create resources in your subscription.
- A GitHub account with an active Copilot license.
- (Optional) A Claude Pro/Max account for the fallback provider — see the ToS
  caveat in `MIGRATION.md` before relying on it.

Set some variables:

```bash
RG=opencode-rg
LOC=westeurope
ACR=opencodeacr$RANDOM        # must be globally unique, lowercase alphanumeric
APP=opencode-$RANDOM          # becomes <APP>.azurewebsites.net
PLAN=opencode-plan
IMG=opencode:latest
```

## 1. Build and push the image (Azure Container Registry)

```bash
az group create -n "$RG" -l "$LOC"
az acr create  -n "$ACR" -g "$RG" --sku Basic --admin-enabled true

# Build from this directory (the one holding the Dockerfile) in the cloud —
# no local Docker needed.
az acr build -r "$ACR" -t "$IMG" .
```

## 2. Create the App Service plan and web app

App Service Free/Shared tiers cannot run a custom container or stay always-on.
Use **B1** (Basic) or higher; your monthly credits cover it.

```bash
az appservice plan create -n "$PLAN" -g "$RG" --is-linux --sku B1

ACR_SERVER=$(az acr show -n "$ACR" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)

az webapp create -n "$APP" -g "$RG" -p "$PLAN" \
  --deployment-container-image-name "$ACR_SERVER/$IMG"

az webapp config container set -n "$APP" -g "$RG" \
  --container-image-name "$ACR_SERVER/$IMG" \
  --container-registry-url "https://$ACR_SERVER" \
  --container-registry-user "$ACR_USER" \
  --container-registry-password "$ACR_PASS"
```

## 3. Critical app settings

This workload is **stateful and single-instance** — session state and the git
working tree live on disk. Do not scale out.

```bash
az webapp config appsettings set -n "$APP" -g "$RG" --settings \
  WEBSITES_PORT=8080 \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=true \
  WEBSITES_CONTAINER_START_TIME_LIMIT=600 \
  NTFY_TOPIC=            # optional: set to enable phone notifications

# WebSockets on (opencode web streams), Always On (keep container warm),
# exactly one worker (state is not shared across instances).
az webapp config set -n "$APP" -g "$RG" \
  --web-sockets-enabled true --always-on true --number-of-workers 1

# Sticky sessions.
az webapp update -n "$APP" -g "$RG" --set clientAffinityEnabled=true

# Do not autoscale.
az appservice plan update -n "$PLAN" -g "$RG" --number-of-workers 1
```

- `WEBSITES_ENABLE_APP_SERVICE_STORAGE=true` persists `/home` (where auth
  tokens and workspace live). Without it, `opencode auth login` would be lost
  on every restart.

## 4. Turn on Entra ID authentication (Layer A)

Portal path (simplest): **App Service → Authentication → Add identity provider
→ Microsoft**. Choose *Workforce (current tenant)*, create a new app
registration, and set:

- **Restrict access**: *Require authentication*.
- **Unauthenticated requests**: *HTTP 302 redirect to log in*.
- **Allowed token audiences / tenant**: your tenant only. Optionally restrict
  to specific users/groups via the app registration's *Enterprise application →
  Users and groups* with *Assignment required = Yes*.

CLI equivalent (v2 auth):

```bash
az webapp auth microsoft update -n "$APP" -g "$RG" \
  --tenant-id "$(az account show --query tenantId -o tsv)" \
  --client-id "<app-registration-client-id>" \
  --client-secret "<app-registration-secret>"

az webapp auth update -n "$APP" -g "$RG" \
  --enabled true --action RedirectToLoginPage \
  --redirect-provider azureactivedirectory
```

After this, hitting the URL forces a Microsoft sign-in before OpenCode loads.

## 5. One-time model login inside the container (Layer B)

Open a shell in the running container — Portal: **App Service → SSH**, or:

```bash
az webapp ssh -n "$APP" -g "$RG"
```

Inside the container:

```bash
opencode auth login
# → select "GitHub Copilot"
# → it prints a code and https://github.com/login/device
# → open that on your laptop, enter the code, approve
```

Optional fallback provider (read the ToS caveat first):

```bash
opencode auth login
# → select "Anthropic" → "Claude Pro/Max"  (browser OAuth)
```

Verify:

```bash
opencode auth list          # should list github-copilot (and anthropic if added)
opencode models | head       # confirm the exact model slugs, then reconcile
                             # opencode.json / rate-limit-fallback.json if they differ
```

`auth.json` now sits in `/home/.local/share/opencode/` and persists.

## 6. Use it

```bash
az webapp restart -n "$APP" -g "$RG"
```

Browse to `https://<APP>.azurewebsites.net`, sign in with your Microsoft
account, and the OpenCode chat UI loads. Switch models mid-session with
**Ctrl+M**; rate-limit fallback is automatic once the fallback plugin is
vendored (see `MIGRATION.md`).

## Notes, limits, gotchas

- **`/home` is Azure Files (SMB).** `git`, `npm`, and node startup on it are
  noticeably slower than local disk. Fine for interactive use; clone big repos
  into `/home/workspace` and expect first operations to lag.
- **Single instance is mandatory.** OpenCode holds session state on disk and in
  memory. Scaling out would split sessions across containers. Keep one worker,
  sticky sessions on.
- **Always On is required** so the container does not idle-stop and drop your
  session.
- **Copilot premium requests.** Claude and GPT-5-class models on Copilot consume
  premium-request quota on your Copilot plan. Watch usage; the `small_model`
  (title generation, etc.) is set to a cheaper Copilot model to limit burn.
- **Defense in depth.** If you want a second gate behind Easy Auth, set
  `OPENCODE_SERVER_PASSWORD` (app setting) — the web UI will prompt for it too.
- **Cost.** B1 runs ~24×7; a monthly Visual Studio/MSDN credit covers it with
  room to spare. Stop the app (`az webapp stop`) when unused to save credit.

### Alternative host: Azure Container Apps

Container Apps also supports built-in Entra auth ("Easy Auth") and is more
container-native (revisions, Azure Files volume mounts). Use `min-replicas 1`
(never scale to zero — it kills session state) and mount an Azure Files share at
`/home` for persistence. App Service is chosen here because its Authentication
blade and SSH console make first-time setup and the one-time OAuth login the
least fiddly.
