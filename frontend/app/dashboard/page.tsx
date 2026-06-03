'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import AgentCard, { Agent } from '../../components/AgentCard';
import LiveLog from '../../components/LiveLog';
import ThemeToggle from '../../components/ThemeToggle';

type RunRow = {
  id: string;
  status: string;
  current_step: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type EventRow = {
  id: number;
  event_type: string;
  payload: Record<string, any>;
  created_at: string;
};

const ACTIVE_STATUSES = new Set(['queued', 'running', 'stopping']);
const POLL_INTERVAL = 2000;
const STOP_STALE_MS = 30000;
const QUEUE_STALE_MS = 3 * 60000;

export default function DashboardPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunRow | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopRequestedAtRef = useRef<number | null>(null);
  const forceCancellingRef = useRef(false);

  // ─── Load agents + detect any active run ──────
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch('/api/agents', { cache: 'no-store' });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || 'Unable to load agents');

        const list: Agent[] = payload.agents || [];
        setAgents(list);
        setDashboardError(null);
        setLoading(false);

        if (list.length) {
          const arRes = await fetch(`/api/agents/${list[0].id}/active-run`, { cache: 'no-store' });
          const arData = await arRes.json();
          if (arRes.ok && arData.run) {
            setActiveRunId(arData.run.id);
            setRun(arData.run as RunRow);
            if (arData.run.status === 'stopping') stopRequestedAtRef.current = Date.now();
          }
        }
      } catch (error: any) {
        setDashboardError(error.message);
        setLoading(false);
      }
    })();
  }, []);

  // ─── Poll run status + events via API ──────
  useEffect(() => {
    if (!activeRunId) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    const poll = async () => {
      try {
        const res = await fetch(`/api/runs/${activeRunId}`, { cache: 'no-store' });
        if (res.status === 404) {
          setRun(null);
          setEvents([]);
          setActiveRunId(null);
          stopRequestedAtRef.current = null;
          forceCancellingRef.current = false;
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          return;
        }
        const data = await res.json();
        if (!res.ok) return;

        const runData = data.run as RunRow;
        if (runData) setRun(runData);

        const eventsData = (data.events || []) as EventRow[];
        setEvents((prev) => (eventsData.length >= prev.length ? eventsData : prev));

        // Stale-run watchdog.
        if (runData?.status === 'stopping' || runData?.status === 'queued') {
          const lastEventTime = eventsData.length
            ? new Date(eventsData[eventsData.length - 1].created_at).getTime()
            : 0;
          const runActivityTime = Math.max(
            lastEventTime,
            runData.started_at ? new Date(runData.started_at).getTime() : 0,
            runData.created_at ? new Date(runData.created_at).getTime() : 0,
            stopRequestedAtRef.current || 0,
          );
          const staleLimit = runData.status === 'queued' ? QUEUE_STALE_MS : STOP_STALE_MS;
          if (!forceCancellingRef.current && runActivityTime > 0 && Date.now() - runActivityTime >= staleLimit) {
            forceCancellingRef.current = true;
            const r = await fetch(`/api/runs/${activeRunId}/stop?force=1`, { method: 'POST' });
            const p = await r.json();
            if (r.ok) {
              setRun((prev) => prev ? { ...prev, status: p.status || 'cancelled', current_step: 'cancelled', finished_at: new Date().toISOString() } : prev);
              stopRequestedAtRef.current = null;
            }
            forceCancellingRef.current = false;
          }
        }

        if (runData && !ACTIVE_STATUSES.has(runData.status)) {
          stopRequestedAtRef.current = null;
          forceCancellingRef.current = false;
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch {
        // silent retry
      }
    };

    poll();
    pollRef.current = setInterval(poll, POLL_INTERVAL);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [activeRunId]);

  // ─── Handlers ─────────────────────────────────
  const startRun = useCallback(async (agentId: string, category: string = 'all') => {
    if (run && ACTIVE_STATUSES.has(run.status)) {
      throw new Error('An agent run is already active.');
    }
    const res = await fetch(`/api/agents/${agentId}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to start run.');
    if (!data.run_id) throw new Error('Run was not created.');

    setActiveRunId(data.run_id);
    setEvents([]);
    setDashboardError(null);
    setRun({
      id: data.run_id,
      status: data.status || 'queued',
      current_step: null,
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
    });
  }, [run]);

  const stopRun = useCallback(async () => {
    if (!activeRunId) return;
    const res = await fetch(`/api/runs/${activeRunId}/stop`, { method: 'POST' });
    const payload = await res.json();
    if (!res.ok) {
      setDashboardError(payload.error || 'Failed to stop run.');
      return;
    }
    setDashboardError(null);
    if (payload.status === 'stopping') stopRequestedAtRef.current = Date.now();
    else { stopRequestedAtRef.current = null; forceCancellingRef.current = false; }
    setRun((prev) => prev ? {
      ...prev,
      status: payload.status || 'stopping',
      current_step: payload.status === 'cancelled' ? 'cancelled' : prev.current_step,
      finished_at: payload.status === 'cancelled' ? new Date().toISOString() : prev.finished_at,
    } : prev);
  }, [activeRunId]);

  // ─── Derived state ────────────────────────────
  const isRunning = run ? ACTIVE_STATUSES.has(run.status) : false;

  const runDuration = useMemo(() => {
    if (!run?.started_at) return null;
    const start = new Date(run.started_at).getTime();
    const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
    const seconds = Math.max(0, Math.floor((end - start) / 1000));
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }, [run?.started_at, run?.finished_at, events.length]);

  const publishedCount = useMemo(
    () => events.filter((e) => e.event_type === 'STEP_DONE' && e.payload?.step === 'publish' && e.payload?.ok).length,
    [events],
  );

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="aurora" aria-hidden><span /></div>
        <div className="spinner" />
      </div>
    );
  }

  return (
    <main className="page-wrap">
      <div className="aurora" aria-hidden><span /></div>

      {/* ─── Top bar ─── */}
      <nav className="navbar glass">
        <div className="navbar-brand">
          <div className="navbar-brand-icon">⚡</div>
          <div className="navbar-brand-text">
            <span className="navbar-title">Shortly AI</span>
            <span className="navbar-sub">▸ PLAYER 1 · NEWS BOT</span>
          </div>
        </div>
        <ThemeToggle />
      </nav>

      {dashboardError && <div className="form-error">{dashboardError}</div>}

      {/* ─── Agent Cards ─── */}
      <div className="grid grid-2">
        {agents.map((agent) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            isRunning={isRunning}
            runStatus={run?.status}
            currentStep={run?.current_step || undefined}
            onStartRun={(category: string) => startRun(agent.id, category)}
            onStopRun={stopRun}
          />
        ))}
        {!agents.length && (
          <div className="card glass">
            <div className="empty-state">
              <div className="empty-state-icon">🤖</div>
              <div className="empty-state-text">No agents configured yet.</div>
            </div>
          </div>
        )}
      </div>

      {/* ─── Run Stats ─── */}
      {run && (
        <div className="card glass run-panel">
          <div className="run-header">
            <h3>◈ Current Run</h3>
            <span className={`badge badge-${run.status}`}>
              {isRunning && <span className="status-dot status-dot-running" />}
              {run.status}
            </span>
          </div>
          <div className="run-stats">
            <div className="run-stat">
              <div className="run-stat-icon">🚀</div>
              <div className="run-stat-value">{publishedCount}</div>
              <div className="run-stat-label">Published</div>
            </div>
            <div className="run-stat">
              <div className="run-stat-icon">⚡</div>
              <div className="run-stat-value">{events.length}</div>
              <div className="run-stat-label">Events</div>
            </div>
            <div className="run-stat">
              <div className="run-stat-icon">⏱️</div>
              <div className="run-stat-value">{runDuration || '0m 0s'}</div>
              <div className="run-stat-label">Duration</div>
            </div>
          </div>
        </div>
      )}

      {/* ─── Live Log ─── */}
      <LiveLog events={events} />

      <footer className="page-footer">★ SHORTLY AI ★ INSERT COIN TO PLAY ★</footer>
    </main>
  );
}
