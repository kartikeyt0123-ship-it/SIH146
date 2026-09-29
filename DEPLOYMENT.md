# Deploying ChainLens

For a URL that stays up for months — on a slide, in a report, clicked by someone who has
never met you.

Everything here is built and tested. The only step left is the one that needs your
account: I cannot create or hold credentials for a hosting provider.

---

## Read this first

This build has **one shared password and no user accounts.** Anyone who has it can read
every case, download every uploaded file and export all evidence. That is a deliberate
scope decision for a prototype, not an oversight — but it means a deployed instance is
only as private as that password.

The application refuses to start on a public address without one:

```
ChainLens refused to start.
Refusing to bind to 0.0.0.0 without authentication.
```

It also rejects passwords under 12 characters. A public URL gets scanned within minutes
of going live.

---

## Why not Vercel or Netlify

They run stateless functions on an ephemeral filesystem. ChainLens keeps a SQLite
database, an archive of uploaded files and generated exports on disk, and runs analysis
on a background thread that outlives the request. On serverless, the database vanishes
between invocations and the worker thread dies when the function returns.

You need a container host with a persistent volume. Fly.io is the recommendation below.

---

## Option A — Fly.io (recommended)

Free allowance covers a small always-on machine. Volumes persist across deploys.

### 1. Install and sign in

```bash
# Windows PowerShell
iwr https://fly.io/install.ps1 -useb | iex
fly auth signup      # or: fly auth login
```

### 2. Create the app and its volume

From the repository root, where `fly.toml` lives:

```bash
fly launch --no-deploy --name chainlens-<yourname>
fly volumes create chainlens_data --size 1 --region bom
```

Pick a region near your audience — `bom` is Mumbai. Change `primary_region` in
`fly.toml` to match.

### 3. Set the password

```bash
fly secrets set CHAINLENS_AUTH_PASSWORD="$(python -c 'import secrets;print(secrets.token_urlsafe(18))')"
fly secrets set CHAINLENS_SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
```

Print the password and save it somewhere you will still have in three months:

```bash
fly ssh console -C "printenv CHAINLENS_AUTH_PASSWORD"
```

Or just choose your own:

```bash
fly secrets set CHAINLENS_AUTH_PASSWORD="pick-something-long-and-memorable"
```

### 4. Deploy

```bash
fly deploy
```

First build takes about five minutes — it compiles the frontend and installs the Python
dependencies. Your URL will be `https://chainlens-<yourname>.fly.dev`.

### 5. Confirm

```bash
fly status
curl https://chainlens-<yourname>.fly.dev/api/health
```

`/api/health` answers without a login, so it works as an uptime check.

---

## Option B — Railway

Volumes and a long-running process, deployable from a Git repository.

1. Push this repository to GitHub.
2. At [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**.
3. Railway detects the `Dockerfile` automatically.
4. **Variables** → add `CHAINLENS_AUTH_PASSWORD` and `CHAINLENS_SESSION_SECRET`.
5. **Settings → Volumes** → mount a volume at `/data`. Without this, cases are lost on
   every redeploy.
6. **Settings → Networking → Generate Domain**.

Railway's trial credit runs out after a few weeks of always-on use; check the plan if the
link must survive three months.

---

## Option C — Hugging Face Spaces (free, no card)

Good when the link matters more than the stored data.

1. Create a Space → SDK **Docker** → visibility **Public**.
2. Push this repository to it.
3. **Settings → Variables and secrets** → add `CHAINLENS_AUTH_PASSWORD`.
4. In the Space's `README.md` front matter, set `app_port: 8000`.

**Storage is ephemeral without a paid upgrade.** The Space restarts periodically and
`/data` is wiped — the app keeps working, but saved cases disappear. For a demo where a
judge uploads the sample themselves, that is fine; for anything you need to persist, use
Fly or Railway.

---

## Option D — Any Docker host

```bash
docker build -t chainlens .
docker run -d --name chainlens \
  -p 8000:8000 \
  -e CHAINLENS_AUTH_PASSWORD="pick-something-long" \
  -v chainlens_data:/data \
  chainlens
```

Put it behind a TLS-terminating reverse proxy. The session cookie is marked `Secure`
automatically when the connection is HTTPS, including through a proxy that sets
`X-Forwarded-Proto`.

---

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `CHAINLENS_AUTH_PASSWORD` | **Yes** for any public bind | Operator password; minimum 12 characters |
| `CHAINLENS_SESSION_SECRET` | Recommended | Cookie signing key. Generated and stored in `/data` if unset — set it explicitly so sessions survive a volume reset |
| `CHAINLENS_DATA_DIR` | No | Writable state. `/data` in the image |
| `CHAINLENS_MODEL_DIR` | No | Model bundle. Baked into the image |
| `CHAINLENS_FRONTEND_DIST` | No | Compiled frontend. Baked into the image |
| `CHAINLENS_SESSION_TTL` | No | Session lifetime in seconds; default 43200 (12 h) |
| `CHAINLENS_MAX_UPLOAD_BYTES` | No | Default 256 MB |
| `CHAINLENS_ENABLE_DOCS` | No | Leave unset. The FastAPI docs UI loads from a CDN |
| `HOST` / `PORT` | No | Set by the platform; honoured automatically |

No API keys, no database URL, no third-party services — the application makes no
outbound network requests.

---

## What ships in the image

Included: application code, compiled frontend, Python dependencies, the trained model
bundle, and `demo_mixed_patterns.csv` so a visitor can try it immediately.

**Excluded deliberately** (see `.dockerignore`): `ps3_transactions`, `ps4_ground_truth`
and the demo answer key. The supplied dataset and its answer key are never baked into a
public image. A visitor uploads their own file.

---

## Linux

The container is the Linux deliverable. It runs Debian slim, Python 3.12, as a non-root
user (uid 10001), writing only to `/data`.

The application code carries no Windows-specific assumptions: paths use `pathlib`
throughout, every text file is opened with an explicit encoding, there are no hardcoded
separators or drive letters, and the single `chmod` call is wrapped for platforms that
ignore it.

To confirm the offline requirement on Linux, run the built image with networking
disabled — the application is fully functional with no route to the internet:

```bash
docker build -t chainlens .
docker run --rm --network none -e CHAINLENS_AUTH_PASSWORD=offline-verification-test \
  chainlens python -m pytest
```

**Not yet verified directly**: the image has not been built and run on a Linux host from
this machine, because neither Docker nor WSL is installed here. The first `fly deploy`
or `docker build` is therefore also the first real Linux execution. The code audit above
is a strong signal, not a substitute for that run — check the deploy logs.

---

## After deploying

Confirm these four things, in order:

1. `https://<your-url>/api/health` returns JSON **without** a login.
2. `https://<your-url>/` shows the **password screen**, not the application.
3. A wrong password is rejected; the correct one lets you in.
4. Upload `demo_mixed_patterns.csv`, run the analysis, open the top alert. Expect
   `COLLECTOR` as a high-priority collection pattern and `EXCHANGE` held at medium.

If step 2 shows the application instead of a login, **the password is not set** — take
the instance down and fix it before sharing the link.

### Keeping it alive for months

- Fly: `min_machines_running = 1` in `fly.toml` (already set) prevents cold starts.
- Watch the free allowance; a stopped machine means a dead link on your slide.
- The model bundle is inside the image, so a redeploy never loses it.
- Cases live on the volume. Deleting the volume deletes them permanently.

### For the SIH demo itself

Run it locally. The PRD asks for a disconnected demonstration, and pulling the network
cable while the application keeps working is a stronger argument than any URL. Use the
deployment as the link on your slide — for the people who watch later.
