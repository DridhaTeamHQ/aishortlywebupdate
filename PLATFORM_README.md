# Unified Agent Platform

This repository includes a deployable multi-service platform:

- `frontend/`: Next.js dashboard with Supabase auth and run controls
- `backend/`: FastAPI control-plane scaffolding
- `platform/worker/`: worker integration code
- `platform/browser-stream/`: browser stream placeholder
- `platform/supabase/schema.sql`: Supabase schema and seed data
- `render.yaml`: Render Blueprint

## Worker Repo Path

The worker runs the checked-in orchestrator code from this repository by default.

- Default worker repo path: `.`
- Optional override: `AI_AGENT_REPO_PATH`
- Compatibility checkout: `external/ai-agent-browser` can still be used locally, but production no longer depends on that nested repo path

## Quick Setup

1. Create a Supabase project
2. Run SQL from `platform/supabase/schema.sql`
3. Deploy with `render.yaml`, or use Vercel for the dashboard and Railway/Render for the worker
4. Configure environment variables:
   - Supabase URL and keys on frontend and worker
   - CMS/OpenAI values on the worker
   - `PLATFORM_ENCRYPTION_KEY` where encrypted user secrets are used
   - `BROWSER_STREAM_BASE_URL` only if you enable browser streaming
   - `AI_AGENT_REPO_PATH` only if you intentionally override the default repo root

## API Contracts

- `GET /api/agents`
- `POST /api/agents/{agent_id}/runs`
- `POST /api/runs/{run_id}/stop`
- `GET /api/runs/{run_id}`
- `POST /api/agents/{agent_id}/schedules`
- `DELETE /api/agents/{agent_id}/schedules/{schedule_id}`
- `POST /api/secrets/me`

## Notes

- Worker emits live events to `agent_run_events`
- Orchestrator supports runtime cancel checks and structured step events
- Browser streaming is scaffolded but optional
