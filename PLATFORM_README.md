# Unified Agent Platform

This repository now includes a deployable multi-service platform:

- `frontend/`: Next.js dashboard (Supabase auth, start/stop, run timeline, stream iframe)
- `backend/`: FastAPI control plane
- `platform/worker/`: Redis RQ worker executing the orchestrator + cron scheduler
- `platform/browser-stream/`: browser stream service placeholder
- `platform/supabase/schema.sql`: Supabase schema + RLS + seed
- `render.yaml`: Render Blueprint

The worker is designed to run the real external agent repo:
- `https://github.com/DridhaTeamHQ/ai-agent-browser.git`
- During Render build, the worker clones that repo into `external/ai-agent-browser`
- `worker_tasks.py` then loads `core.orchestrator` from that external repo and applies dashboard runtime controls

## Quick setup

1. Create a Supabase project.
2. Run SQL in `platform/supabase/schema.sql`.
3. Provision Render services using `render.yaml`.
4. Configure env vars:
   - Supabase keys/URL on API + worker + frontend
   - `PLATFORM_ENCRYPTION_KEY` on API + worker (same value)
   - CMS/OpenAI vars on worker
   - `CONTROL_API_BASE_URL` on frontend
   - `BROWSER_STREAM_BASE_URL` on API + worker
   - `AI_AGENT_REPO_PATH` on worker if you want to override the default external repo checkout path

## API contracts

- `GET /api/agents`
- `POST /api/agents/{agent_id}/runs`
- `POST /api/runs/{run_id}/stop`
- `GET /api/runs/{run_id}`
- `POST /api/agents/{agent_id}/schedules`
- `DELETE /api/agents/{agent_id}/schedules/{schedule_id}`
- `POST /api/secrets/me`

## Notes

- Worker emits run events to `agent_run_events`.
- Orchestrator now supports runtime cancel checks and structured step events.
- Browser stream endpoint is scaffolded; connect it to your preferred remote browser provider for full live Chromium streaming.
