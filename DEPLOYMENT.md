# Deployment Guide

Shortly AI is designed as a split deployment:

- Vercel or Render Web: hosts the Next.js dashboard in `frontend/`
- Render Worker or Railway Worker: hosts the Python Playwright worker from the repo root
- Supabase: stores users, agents, runs, and realtime run events

The dashboard and the worker coordinate through Supabase, so production does not require a direct HTTP connection between them.

## Vercel

Recommended Vercel project roots:

1. Repository root
   - Leave `Root Directory` empty
   - Vercel uses the root `package.json`, which forwards build/start commands into `frontend/`

2. `frontend`
   - Point the Vercel project directly at `frontend`

Compatibility option:

3. `web`
   - The repository now includes a `web/` wrapper for older Vercel projects that were already pinned to `web`
   - If you use this option, keep `Include files outside the root directory in the Build Step` enabled so the wrapper can access `../frontend`

Required Vercel environment variables:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

## Render

The repo includes a production-ready [render.yaml](C:\Users\Tamada\Desktop\Shortly AI Agent\render.yaml) Blueprint with:

- `shortly-dashboard` web service from `frontend/`
- `shortly-worker` background worker from the repo root

Required worker environment variables:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENAI_API_KEY`
- `CMS_URL`
- `CMS_EMAIL`
- `CMS_PASSWORD`

Default worker runtime values in the Blueprint:

- `WORKER_POLL_INTERVAL=5`
- `AI_AGENT_REPO_PATH=.`

## Railway

The repo root also includes [railway.json](C:\Users\Tamada\Desktop\Shortly AI Agent\railway.json) for the Python worker:

- Build: `pip install --prefer-binary -r requirements.txt && playwright install chromium`
- Start: `python worker.py`

Required Railway environment variables:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENAI_API_KEY`
- `CMS_URL`
- `CMS_EMAIL`
- `CMS_PASSWORD`

Recommended Railway runtime values:

- `WORKER_POLL_INTERVAL=5`
- `AI_AGENT_REPO_PATH=.`

## Production Notes

- Production defaults now point the worker at the checked-in repository root instead of depending on the optional `external/ai-agent-browser` checkout.
- The dashboard starts and stops runs through Supabase-backed Next.js API routes, so the web deployment does not need to call the worker directly.
- If you migrate an older Vercel project that previously used `web` as a root directory, the new `web/` wrapper keeps that deployment path working without moving the real app out of `frontend/`.
