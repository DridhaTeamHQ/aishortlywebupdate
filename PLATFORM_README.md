# Unified Agent Platform

This repository now includes a deployable multi-service platform:

- `web/`: Next.js dashboard (Supabase auth, start/stop, run timeline, stream iframe)
- `platform/api/`: FastAPI control plane
- `platform/worker/`: Redis RQ worker executing the orchestrator + cron scheduler
- `platform/browser-stream/`: browser stream service placeholder
- `platform/supabase/schema.sql`: Supabase schema + RLS + seed
- `render.yaml`: Render Blueprint

## Quick setup

1. Create a Supabase project.
2. Run SQL in `platform/supabase/schema.sql`.
3. Provision Render services using `render.yaml`.
4. Configure env vars:
   - Supabase keys/URL on API + worker + web
   - `PLATFORM_ENCRYPTION_KEY` on API + worker (same value)
   - CMS/OpenAI vars on worker
   - `NEXT_PUBLIC_API_BASE_URL` on web
   - `BROWSER_STREAM_BASE_URL` on API + worker

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

