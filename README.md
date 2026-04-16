# Shortly AI

Shortly AI is a split production platform built around Supabase-backed agent runs.

- `frontend/`: Next.js dashboard for auth, run controls, and live status
- repo root: Python Playwright worker that claims queued runs and executes the agent
- `platform/`: supporting deployment, schema, and worker integration files

## Production Targets

This repository is configured to support:

- Vercel for the dashboard
- Render for the dashboard and worker via [render.yaml](C:\Users\Tamada\Desktop\Shortly AI Agent\render.yaml)
- Railway for the worker via [railway.json](C:\Users\Tamada\Desktop\Shortly AI Agent\railway.json)

Deployment details live in [DEPLOYMENT.md](C:\Users\Tamada\Desktop\Shortly AI Agent\DEPLOYMENT.md).

## Local Development

Install Python and frontend dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
npm install
npm --prefix frontend install
```

Copy [env.example](C:\Users\Tamada\Desktop\Shortly AI Agent\env.example) to `.env` and fill in your values.

Run the worker:

```bash
python worker.py
```

Run the dashboard:

```bash
npm run dev
```

## Deployment Notes

- Recommended Vercel project roots: repository root or `frontend`
- Compatibility Vercel root: `web`
- Production worker default repo path: `.`
- Optional legacy worker override: `AI_AGENT_REPO_PATH=external/ai-agent-browser`

The `web/` folder is a compatibility wrapper for older Vercel projects that were previously configured with `web` as the root directory. The real Next.js application still lives in `frontend/`.
