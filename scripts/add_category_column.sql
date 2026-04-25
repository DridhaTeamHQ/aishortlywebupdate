-- Adds a per-run category filter to agent_runs.
-- Run this once in Supabase SQL editor before deploying the dropdown.
ALTER TABLE agent_runs
  ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'all';
