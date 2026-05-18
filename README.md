# Contact Circles

A private web app to keep track of the people in your life. Organise them into **circles** (Family, Close Friends, School, Work…), where one person can belong to **multiple circles**. Telegram login. A daily bot DM nudges you to reach out to whoever's overdue, with one-tap "talked / snooze / skip" buttons.

Built to match the existing FastAPI + SQLite + Coolify pattern (`prayer-web`, `finance-web`).

## Run locally

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in BOT_TOKEN, SESSION_SECRET
mkdir -p data && echo "DB_PATH=./data/app.db" >> .env

# In @BotFather:  /newbot → grab token → /setdomain → http://localhost:8080
.venv/bin/python -m src.main
```

Then visit `http://localhost:8080/login`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## Deploy to Coolify (single host on EC2)

Follow `../COOLIFY_DEPLOY_GUIDE.md`. Specific steps for this app:

1. **BotFather setup**
   - `/newbot` → save token as `BOT_TOKEN`.
   - `/setdomain` → set to your Coolify-issued domain (e.g. `circles.bode1.site`).
   - `/setjoingroups` → Disable.
   - The Telegram Login Widget will refuse to render until the domain matches.

2. **Push to GitHub** (public repo — uses the Public source flow in the guide).

3. **Create the Coolify app** via API (Steps A–D of the guide). Then via API PATCH:
   - `watch_paths: "src/**\nrequirements.txt\nDockerfile"`
   - `docker_compose_location: /docker-compose.yml`

4. **Env vars** (all with `is_preview: false` — the guide's gotcha):
   - `BOT_TOKEN` — from BotFather
   - `SESSION_SECRET` — `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - `DB_PATH=/data/app.db`
   - `APP_BASE_URL=https://<your-coolify-domain>`
   - `TZ=Africa/Cairo`
   - `ALLOWED_TELEGRAM_IDS=5904148250,...` — optional comma-separated whitelist; empty = anyone with a Telegram account can log in

5. **Volume** — `docker-compose.yml` declares `contact-circles-data:/data`. Coolify will create it on first deploy. SQLite + WAL survives redeploys.

6. **GitHub webhook** (Step 4 of the guide) so pushes auto-deploy.

7. **Pre/post deploy Telegram notifications** (optional) — same pattern as the guide. `curl` is already installed in the Dockerfile.

## How the reminder math works

Each circle has a `default_cadence_days`. When a contact belongs to multiple circles, the effective cadence is the **minimum** across them (the closer relationship wins). The hourly sweep walks every user, and for users whose local `digest_hour` matches the current local hour, it picks the top 5 most-overdue contacts (sorted by `days_since_contact / cadence_days`) and DMs them with inline buttons. See `src/reminders.py`.

## Architecture

```
single Docker container
├── FastAPI (HTMX-rendered pages on :8080)
├── aiogram bot polling Telegram for /start + callback queries
├── APScheduler (hourly sweep job)
└── SQLite at /data/app.db (WAL mode, FK on)
```

All three run on one `asyncio` event loop. See `src/main.py`.
