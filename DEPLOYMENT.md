# Split Deployment Architecture: Vercel + Railway

The platform uses a fully decoupled architecture split between Vercel (Frontend) and Railway (Backend worker). 

Because all long-running tasks and queues are managed via Supabase, the Vercel serverless layer and Railway worker layer **never talk directly to each other via HTTP**. This instantly eliminates CORS issues, network latency problems, API Gateway setups, and Vercel's strict 300-second execution timeouts.

## 1. Supabase (Database & Realtime Queue)

Supabase essentially acts as the connective tissue for the entire application.
- The `runs` and `agent_run_events` tables act as the Job Queue.
- **Frontend** pushes to Supabase and listens via Realtime sockets.
- **Backend** long-polls Supabase and executes the work.

## 2. Vercel Deployment (Frontend)

Vercel hosts the Next.js user interface and lightweight Serverless API routes (which only read/write to the Supabase database).

1. **Connect GitHub**: Import the repository in Vercel.
2. **Zero-Config Build**: Keep the "Root Directory" blank (at the repository root). The `package.json` at the root automatically proxies `npm run build` directly into the `frontend/` subdirectory, so Vercel builds the Next.js app flawlessly out of the box.
3. **Environment Variables**: Add the following explicitly in the Vercel Dashboard:
   - `NEXT_PUBLIC_SUPABASE_URL` = <your-supabase-project-url>
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY` = <your-supabase-public-anon-key>
   - `SUPABASE_SERVICE_ROLE_KEY` = <your-supabase-secret-service-role-key>

*Note: The frontend needs the service role key specifically for the Next.js API routes (`app/api/agents/route.ts`) to query the `agents` and `profiles` tables with admin-bypass row-level security.*

## 3. Railway Deployment (Backend)

Railway hosts the long-running Python worker that uses Playwright and OpenAI to run complex agent jobs.

1. **New Service**: In Railway, create a new deployment directly from the GitHub repository.
2. **Automatic Detection**: Railway will automatically read `railway.json` and `nixpacks.toml` at the root directory. This configures Railway to:
   - Install Python 3.11 and GCC
   - Run `pip install -r requirements.txt`
   - Run Playwright browser dependency installation (`playwright install chromium --with-deps`)
   - Start the background task queue using the command: `python worker.py`
3. **Environment Variables**: Add the following in the Railway Dashboard:
   - `SUPABASE_URL` = <your-supabase-project-url>
   - `SUPABASE_SERVICE_ROLE_KEY` = <your-supabase-secret-service-role-key>
   - `OPENAI_API_KEY` = <your-openai-key>
   - `WORKER_POLL_INTERVAL` = `5`

## 4. Communication Flow

Unlike traditional monoliths, Vercel and Railway are not coupled by a direct HTTP endpoint link.

1. **Frontend Workflow**: User clicks "Start Agent" on Vercel. The Next.js API route pushes a new `run` row into the Supabase database and immediately returns `200 OK`.
2. **Backend Workflow**: The Python process on Railway executes an endless loop, polling the Supabase database every 5 seconds. When it sees an unclaimed job, it claims it, executes the OpenAI extraction sequence using Playwright browsers, and writes real-time logs and the final result back to Supabase.
3. **Realtime UI Sync**: The Vercel frontend subscribes to postgres changes using the Supabase Realtime socket client, showing live event logs and status updates to the user seamlessly without ever hitting the Railway server.
