# Unified AI Agent Platform

A fully decoupled, scalable agent platform. The frontend operates on Vercel while a robust Python long-running worker executes browser-based tasks on Railway. They communicate exclusively via Supabase Queues and Realtime.

## 🚀 Deployment Guide

This repository is pre-configured to deploy effortlessly across Vercel and Railway with **zero code changes required**. 

Please see **[DEPLOYMENT.md](DEPLOYMENT.md)** for exact, step-by-step instructions on setting up Vercel (Frontend), Railway (Backend Worker), and Supabase (Database + Queue).

## Setup Local Environment

### 1. Install Dependencies

```bash
# Install Backend worker dependencies
pip install -r requirements.txt
python -m playwright install chromium

# Install Frontend dependencies
npm install
npm --prefix frontend install
```

### 2. Environment Variables
Copy `env.example` to `.env` and fill out the required variables for your setup. The file is cleanly split into Frontend and Backend variables.

### 3. Run Locally

Open two terminal tabs:

**Terminal 1 (Backend Worker)**
```bash
python worker.py
```

**Terminal 2 (Frontend Dashboard)**
```bash
npm run dev
```

## Architecture

**Key Principle**: The Browser Agent is the ONLY decision maker. No external schedulers, databases, or API servers.

### Components

- **main.py**: Controller loop (tries up to 5 articles until one is published)
- **agent/browser_agent.py**: Playwright browser management
- **agent/scraper.py**: News article scraping from BBC/Al Jazeera/NBC
- **agent/summarizer.py**: English summarization (300-360 chars)
- **agent/telugu_writer.py**: Telugu translation with fallbacks
- **agent/category_decider.py**: Simple keyword-based category selection
- **agent/cms_publisher.py**: CMS login, form filling, and publishing

## Features

- **No Database**: All in-memory, no persistence
- **No Scheduler**: Single run, publishes one article
- **No Hard Blockers**: Quality checks warn but never block
- **Visible Browser**: See exactly what's happening
- **Automatic Fallbacks**: Telugu failures → English, publish failures → try next article

## Category Rules

- **Spiritual**: temple, god, puja, astrology
- **Sports**: cricket, match, player, ipl
- **Entertainment**: actor, film, cinema, movie
- **Health**: health, diet, disease, medical
- **International**: Only if clearly foreign (USA, China, etc.)
- **National**: Default for everything else

## Telugu Style

Matches TV9/Eenadu/Sakshi digital news style:
- Natural Telugu (not literal translation)
- Newsroom vocabulary
- Short, punchy sentences
- 300-360 characters for summary

## Troubleshooting

### "OPENAI_API_KEY not found"
- Make sure you created `.env` file (not `env.example`)
- Check that `OPENAI_API_KEY=sk-...` is in `.env`

### "CMS credentials not found"
- Make sure `CMS_URL`, `CMS_EMAIL`, and `CMS_PASSWORD` are in `.env`

### Browser doesn't open
- Make sure Playwright is installed: `playwright install chromium`
- Check that `HEADLESS=false` in `.env`

### "No articles found"
- Check internet connection
- News sources (BBC/Al Jazeera/NBC) might be blocked in your region
- Try running again (different articles may be available)

### CMS login fails
- Verify CMS credentials in `.env`
- Check that `CMS_ROLE` matches exactly (case-sensitive)
- Make sure CMS URL is correct

### Article scraping fails
- Network issues
- News source website structure changed
- Try running again (will try different sources)

## Logs

Check `artifacts/logs/automation.log` for detailed logs of what happened.

## Screenshots

Screenshots are saved to `artifacts/screenshots/` if errors occur.
