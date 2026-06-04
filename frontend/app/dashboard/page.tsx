'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import AgentCard, { Agent } from '../../components/AgentCard';
import LiveLog from '../../components/LiveLog';
import ThemeToggle from '../../components/ThemeToggle';
import { supabase } from '../../lib/supabase';

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
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunRow | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState<string>('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopRequestedAtRef = useRef<number | null>(null);
  const forceCancellingRef = useRef(false);

  // Authenticated fetch — attaches the current Supabase access token.
  const authFetch = useCallback(async (input: string, init?: RequestInit) => {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (!token) {
      router.replace('/login');
      throw new Error('Not authenticated');
    }
    const headers = { ...(init?.headers || {}), Authorization: `Bearer ${token}` };
    return fetch(input, { ...init, cache: 'no-store', headers });
  }, [router]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    router.replace('/login');
  }, [router]);

  // ─── Auth guard ──────
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.replace('/login');
      } else {
        setUserEmail(data.session.user.email || '');
      }
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session) router.replace('/login');
      else setUserEmail(session.user.email || '');
    });
    return () => { listener.subscription.unsubscribe(); };
  }, [router]);

  // ─── Load agents + detect any active run ──────
  useEffect(() => {
    (async () => {
      try {
        const res = await authFetch('/api/agents');
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || 'Unable to load agents');

        const list: Agent[] = payload.agents || [];
        setAgents(list);
        setDashboardError(null);
        setLoading(false);

        if (list.length) {
          const arRes = await authFetch(`/api/agents/${list[0].id}/active-run`);
          const arData = await arRes.json();
          if (arRes.ok && arData.run) {
            setActiveRunId(arData.run.id);
            setRun(arData.run as RunRow);
            if (arData.run.status === 'stopping') stopRequestedAtRef.current = Date.now();
          }
        }
      } catch (error: any) {
        if (error?.message !== 'Not authenticated') setDashboardError(error.message);
        setLoading(false);
      }
    })();
  }, [authFetch]);

  // ─── Poll run status + events via API ──────
  useEffect(() => {
    if (!activeRunId) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    const poll = async () => {
      try {
        const res = await authFetch(`/api/runs/${activeRunId}`);
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

        // The run is over the moment a RUN_FINISHED event lands — stop polling
        // even if the run row's status is lagging.
        const lastEvent = eventsData[eventsData.length - 1];
        if (lastEvent?.event_type === 'RUN_FINISHED') {
          stopRequestedAtRef.current = null;
          forceCancellingRef.current = false;
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          return;
        }

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
            const r = await authFetch(`/api/runs/${activeRunId}/stop?force=1`, { method: 'POST' });
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
  }, [activeRunId, authFetch]);

  // ─── Handlers ─────────────────────────────────
  const startRun = useCallback(async (agentId: string, category: string = 'all') => {
    if (run && ACTIVE_STATUSES.has(run.status)) {
      throw new Error('An agent run is already active.');
    }
    const res = await authFetch(`/api/agents/${agentId}/runs`, {
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
  }, [run, authFetch]);

  const stopRun = useCallback(async () => {
    if (!activeRunId) return;
    try {
      const res = await authFetch(`/api/runs/${activeRunId}/stop`, { method: 'POST' });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        setDashboardError(payload.error || 'Failed to stop run.');
        return;
      }
    } catch {
      setDashboardError('Failed to stop run.');
      return;
    }

    // Stop is final: tear down polling and flip the UI back to idle immediately,
    // independent of any in-flight events from the winding-down worker.
    setDashboardError(null);
    stopRequestedAtRef.current = null;
    forceCancellingRef.current = false;
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setRun((prev) => prev ? {
      ...prev,
      status: 'cancelled',
      current_step: 'cancelled',
      finished_at: new Date().toISOString(),
    } : prev);
    setActiveRunId(null);
  }, [activeRunId, authFetch]);

  // ─── Derived state ────────────────────────────
  // The event stream is the source of truth: a RUN_FINISHED event means the run
  // is over, regardless of any lag in the run row's status field.
  const finishedEvent = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      if (events[i].event_type === 'RUN_FINISHED') return events[i];
    }
    return undefined;
  }, [events]);
  const finalStatus: string | undefined = finishedEvent?.payload?.status;

  const isRunning = useMemo(() => {
    if (finishedEvent) return false;
    if (!run) return false;
    return ACTIVE_STATUSES.has(run.status);
  }, [finishedEvent, run]);

  // Live 1s clock so the duration advances smoothly while a run is active,
  // instead of only updating when a new event arrives.
  const [nowTick, setNowTick] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(() => setNowTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [isRunning]);

  const runDuration = useMemo(() => {
    // Robust start time: prefer started_at, fall back to created_at, then the
    // first event — so the clock is correct even before the worker stamps
    // started_at.
    const startMs = run?.started_at
      ? new Date(run.started_at).getTime()
      : run?.created_at
        ? new Date(run.created_at).getTime()
        : events.length
          ? new Date(events[0].created_at).getTime()
          : 0;
    if (!startMs) return '0m 0s';
    const endMs = isRunning
      ? Date.now()
      : run?.finished_at
        ? new Date(run.finished_at).getTime()
        : finishedEvent
          ? new Date(finishedEvent.created_at).getTime()
          : events.length
            ? new Date(events[events.length - 1].created_at).getTime()
            : Date.now();
    const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000));
    return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.started_at, run?.created_at, run?.finished_at, events, isRunning, nowTick, finishedEvent]);

  const publishedCount = useMemo(
    () => events.filter((e) => e.event_type === 'STEP_DONE' && e.payload?.step === 'publish' && e.payload?.ok).length,
    [events],
  );

  // Badge status: trust the RUN_FINISHED event first, then the run row.
  const displayStatus = useMemo(() => {
    if (finishedEvent) return finalStatus || 'succeeded';
    if (!run) return undefined;
    if (run.status === 'queued' && events.length > 0) return 'running';
    return run.status;
  }, [finishedEvent, finalStatus, run, events.length]);

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
        <div className="navbar-actions">
          {userEmail && <span className="navbar-user" title={userEmail}>{userEmail}</span>}
          <ThemeToggle />
          <button type="button" className="navbar-signout" onClick={signOut}>SIGN OUT</button>
        </div>
      </nav>

      {dashboardError && <div className="form-error">{dashboardError}</div>}

      {/* ─── Agent Cards ─── */}
      <div className="grid grid-2">
        {agents.map((agent) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            isRunning={isRunning}
            runStatus={displayStatus}
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
            <span className={`badge badge-${displayStatus}`}>
              {isRunning && <span className="status-dot status-dot-running" />}
              {displayStatus}
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
              <div className="run-stat-value">{runDuration}</div>
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
