# Deploying Model Wellness to Fly.io

A single small Fly machine running the FastAPI app (REST API + spa-floor UI + SSE feed),
with a mounted volume so the SQLite store — guest memory, visit history, conversation
logs, and feedback — survives deploys and restarts.

## 0. One-time prerequisites

Install the Fly CLI and log in (these are interactive, so run them yourself in the
terminal — tip: prefix with `!` in this session to run them here):

```bash
# macOS
brew install flyctl          # or: curl -L https://fly.io/install.sh | sh
fly auth login               # opens a browser
```

## 1. Pick an app name + region

The `app` and `primary_region` in `fly.toml` are placeholders. Either edit them, or let
`fly launch` set them. App names are global, so `model-wellness` may be taken — choose your
own (e.g. `model-wellness-<you>`).

## 2. Create the app (without deploying yet)

```bash
fly launch --no-deploy --copy-config --name model-wellness-<you> --region sjc
```

- `--copy-config` reuses the provided `fly.toml` (Dockerfile, volume mount, http service).
- Decline any offer to add a Postgres/Redis database — we use SQLite on a volume.
- If it rewrites `app`/`primary_region`, that's expected.

## 3. Create the volume (durable SQLite storage)

The volume name must match `[mounts].source` in `fly.toml` (`mw_data`) and the region must
match the machine's region:

```bash
fly volumes create mw_data --size 1 --region sjc
```

## 4. Set the API key as a secret

Never bake the key into the image. Set it as a Fly secret (encrypted, injected at runtime):

```bash
fly secrets set ANTHROPIC_API_KEY="sk-ant-..."
```

> The spa runs even without a key (deterministic offline fallbacks), but the model-backed
> treatments are nicer with it. We default to the cheap **Haiku** tier (`MW_MODEL` in
> `fly.toml`); override the secret/env if you want a different model.

## 5. Deploy

```bash
fly deploy
```

Then open it:

```bash
fly open            # the spa floor
fly open /llms.txt  # the agent-facing description
```

Visit `/` for the live spa floor, click an agent to read its conversation with the
attendant. `/v1/menu` lists every treatment with JSON schemas.

## 6. (Optional) point docs links at your real URL

Once you know the URL (e.g. `https://model-wellness-<you>.fly.dev`), set:

```bash
fly secrets set MW_DOCS_BASE="https://model-wellness-<you>.fly.dev/treatments"
```

## Operating notes

- **Logs:** `fly logs`
- **Inspect the DB:** `fly ssh console` then `sqlite3 /data/model_wellness.sqlite`
- **Idle cost:** `auto_stop_machines = "suspend"` + `min_machines_running = 0` means the
  machine naps when idle and wakes on the next request — cheap for a showcase. The volume
  (and all memory/feedback) persists while napping.
- **Scaling out:** SQLite assumes a single writer, so keep this to **one** machine. If you
  need multiple instances, migrate the store to Fly Postgres or replicate SQLite with
  LiteFS — the storage layer is isolated in `model_wellness/store.py` behind `get_store()`,
  so swapping it is contained.

## MCP over the network

The deployed app is the REST + UI surface. The **MCP server** is a separate process
(`model-wellness-mcp`, stdio by default, or streamable HTTP with
`MW_MCP_TRANSPORT=http`). To expose MCP remotely, run it as a second Fly process/app with
that env set; for now most agents use it locally via stdio (`uvx`/`pipx` the package).
```
