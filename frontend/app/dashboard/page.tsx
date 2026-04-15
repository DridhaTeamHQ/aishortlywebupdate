'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import AgentCard, { Agent } from '../../components/AgentCard';
import LiveLog from '../../components/LiveLog';
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
const POLL_INTERVAL = 2000; // 2 seconds
const STOP_STALE_MS = 15000;
const QUEUE_STALE_MS = 45000;
const ORPHAN_RUN_MAX_AGE_MS = 10 * 60 * 1000;

export default function DashboardPage() {
  const router = useRouter();
  const [user, setUser] = useState<any>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunRow | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);
  const stopRequestedAtRef = useRef<number | null>(null);
  const forceCancellingRef = useRef(false);

  // ─── Auth check ───────────────────────────────
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.replace('/login');
        return;
      }
      setUser(data.session.user);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session) router.replace('/login');
    });

    return () => { listener.subscription.unsubscribe(); };
  }, [router]);

  // ─── Load agents + detect any active run ──────
  useEffect(() => {
    if (!user) return;
    supabase.auth.getSession().then(async ({ data }) => {
      const accessToken = data.session?.access_token;
      if (!accessToken) {
        router.replace('/login');
        return;
      }

      const response = await fetch('/api/agents', {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || payload.detail || 'Unable to load agents');
      }

      setAgents(payload.agents || []);
      setDashboardError(null);
      setLoading(false);

      // Check for any active run in the database
      const { data: activeRuns } = await supabase
        .from('agent_runs')
        .select('id,status,current_step,created_at,started_at,finished_at')
        .eq('created_by', user.id)
        .in('status', ['queued', 'running', 'stopping'])
        .order('created_at', { ascending: false })
        .limit(1);

      if (activeRuns && activeRuns.length > 0) {
        const activeRun = activeRuns[0] as RunRow;
        const createdAtMs = activeRun.created_at ? new Date(activeRun.created_at).getTime() : 0;
        const isLikelyOrphan =
          (activeRun.status === 'queued' || activeRun.status === 'stopping') &&
          createdAtMs > 0 &&
          (Date.now() - createdAtMs) > ORPHAN_RUN_MAX_AGE_MS;

        if (!isLikelyOrphan) {
          setActiveRunId(activeRun.id);
          setRun(activeRun);
          if (activeRun.status === 'stopping') {
            stopRequestedAtRef.current = Date.now();
          }

          // Load existing events for this run
          const { data: existingEvents } = await supabase
            .from('agent_run_events')
            .select('id,event_type,payload,created_at')
            .eq('run_id', activeRun.id)
            .order('id', { ascending: true });
          if (existingEvents) setEvents(existingEvents);
        }
      }
    })
      .catch((error: Error) => {
        setDashboardError(error.message);
        setLoading(false);
      });
  }, [router, user]);

  // ─── Poll for run status + events (reliable fallback) ──────
  useEffect(() => {
    if (!activeRunId) {
      // Clear any existing poll
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    const poll = async () => {
      try {
        // Fetch run status
        const { data: runData, error: runError } = await supabase
          .from('agent_runs')
          .select('id,status,current_step,created_at,started_at,finished_at')
          .eq('id', activeRunId)
          .single();

        // If the run row no longer exists (commonly surfaced as 406/PGRST116), clear stale UI state.
        if (runError && (runError.code === 'PGRST116' || runError.message?.includes('JSON object requested'))) {
          setRun(null);
          setEvents([]);
          setActiveRunId(null);
          stopRequestedAtRef.current = null;
          forceCancellingRef.current = false;
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          return;
        }

        if (runData) setRun(runData as RunRow);

        // Fetch all events (compare by count to avoid duplicates)
        const { data: eventsData } = await supabase
          .from('agent_run_events')
          .select('id,event_type,payload,created_at')
          .eq('run_id', activeRunId)
          .order('id', { ascending: true });

        if (eventsData) {
          setEvents((prev) => {
            // Only update if we got new events
            if (eventsData.length > prev.length) {
              return eventsData;
            }
            return prev;
          });
        }

        if (runData?.status === 'stopping' || runData?.status === 'queued') {
          const lastEventTime = eventsData && eventsData.length > 0
            ? new Date(eventsData[eventsData.length - 1].created_at).getTime()
            : 0;
          const runActivityTime = Math.max(
            lastEventTime,
            runData.started_at ? new Date(runData.started_at).getTime() : 0,
            runData.created_at ? new Date(runData.created_at).getTime() : 0,
            stopRequestedAtRef.current || 0,
          );

          const staleLimit = runData.status === 'queued' ? QUEUE_STALE_MS : STOP_STALE_MS;
          if (
            !forceCancellingRef.current &&
            runActivityTime > 0 &&
            (Date.now() - runActivityTime) >= staleLimit
          ) {
            forceCancellingRef.current = true;
            const session = (await supabase.auth.getSession()).data.session;
            if (session) {
              const response = await fetch(`/api/runs/${activeRunId}/stop?force=1`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${session.access_token}` },
              });
              const payload = await response.json();
              if (response.ok) {
                setRun((prev) => prev ? {
                  ...prev,
                  status: payload.status || 'cancelled',
                  current_step: 'cancelled',
                  finished_at: new Date().toISOString(),
                } : prev);
                stopRequestedAtRef.current = null;
              }
            }
            forceCancellingRef.current = false;
          }
        }

        // Stop polling if run is finished
        if (runData && !ACTIVE_STATUSES.has(runData.status)) {
          stopRequestedAtRef.current = null;
          forceCancellingRef.current = false;
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
        }
      } catch (err) {
        // Silent fail, will retry next poll
      }
    };

    // Initial fetch
    poll();

    // Start polling
    pollRef.current = setInterval(poll, POLL_INTERVAL);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [activeRunId]);

  // ─── Handlers ─────────────────────────────────
  const startRun = useCallback(async (agentId: string) => {
    const session = (await supabase.auth.getSession()).data.session;
    if (!session) throw new Error('Please sign in again.');

    // Don't start if already running
    if (run && ACTIVE_STATUSES.has(run.status)) {
      throw new Error('An agent run is already active.');
    }

    const res = await fetch(`/api/agents/${agentId}/runs`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${session.access_token}`,
      },
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || data.detail || 'Failed to start run.');
    }

    if (!data.run_id) {
      throw new Error('Run was not created.');
    }

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
    const session = (await supabase.auth.getSession()).data.session;
    if (!session) {
      setDashboardError('Please sign in again.');
      return;
    }

    const response = await fetch(`/api/runs/${activeRunId}/stop`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${session.access_token}` },
    });
    const payload = await response.json();
    if (!response.ok) {
      setDashboardError(payload.error || payload.detail || 'Failed to stop run.');
      return;
    }

    setDashboardError(null);
    if (payload.status === 'stopping') {
      stopRequestedAtRef.current = Date.now();
    } else {
      stopRequestedAtRef.current = null;
      forceCancellingRef.current = false;
    }
    // Optimistic update
    setRun((prev) => prev ? {
      ...prev,
      status: payload.status || 'stopping',
      current_step: payload.status === 'cancelled' ? 'cancelled' : prev.current_step,
      finished_at: payload.status === 'cancelled' ? new Date().toISOString() : prev.finished_at,
    } : prev);
  }, [activeRunId]);

  const signOut = async () => {
    await supabase.auth.signOut();
    router.replace('/login');
  };

  // ─── Derived state ────────────────────────────
  const isRunning = run ? ACTIVE_STATUSES.has(run.status) : false;

  const runDuration = useMemo(() => {
    if (!run?.started_at) return null;
    const start = new Date(run.started_at).getTime();
    const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
    const seconds = Math.floor((end - start) / 1000);
    const min = Math.floor(seconds / 60);
    const sec = seconds % 60;
    return `${min}m ${sec}s`;
  }, [run?.started_at, run?.finished_at, events.length]);

  const publishedCount = useMemo(() => {
    return events.filter(
      (e) => e.event_type === 'STEP_DONE' && e.payload?.step === 'publish' && e.payload?.ok
    ).length;
  }, [events]);

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div className="spinner" />
      </div>
    );
  }

  return (
    <main className="page-wrap">
      {/* ─── Navbar ─── */}
      <nav className="navbar">
        <div className="navbar-brand">
          <div className="navbar-brand-icon">⚡</div>
          <span>Shortly AI</span>
        </div>
        <div className="navbar-actions">
          <span className="text-muted">{user?.email}</span>
          <button className="btn btn-sm" onClick={signOut}>Sign Out</button>
        </div>
      </nav>

      {dashboardError && (
        <div className="form-error" style={{ marginBottom: 20 }}>
          {dashboardError}
        </div>
      )}

      {/* ─── Agent Cards ─── */}
      <div className="grid grid-2" style={{ marginBottom: 20 }}>
        {agents.map((agent) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            isRunning={isRunning}
            runStatus={run?.status}
            currentStep={run?.current_step || undefined}
            onStartRun={() => startRun(agent.id)}
            onStopRun={stopRun}
          />
        ))}
        {!agents.length && (
          <div className="card">
            <div className="empty-state">
              <div className="empty-state-icon">🤖</div>
              <div className="empty-state-text">No agents configured. Apply the Supabase schema to get started.</div>
            </div>
          </div>
        )}
      </div>

      {/* ─── Run Stats ─── */}
      {run && (
        <div className="card" style={{ marginBottom: 20, animation: 'slideUp 0.4s ease-out both' }}>
          <div className="run-header">
            <h3 style={{ margin: 0 }}>📊 Current Run</h3>
            <span className={`badge badge-${run.status}`}>
              {isRunning && <div className="status-dot status-dot-running" style={{ width: 6, height: 6 }} />}
              {run.status}
            </span>
          </div>
          <div className="run-stats">
            <div className="run-stat">
              <div className="run-stat-value">{publishedCount}</div>
              <div className="run-stat-label">Published</div>
            </div>
            <div className="run-stat">
              <div className="run-stat-value">{events.length}</div>
              <div className="run-stat-label">Events</div>
            </div>
            {runDuration && (
              <div className="run-stat">
                <div className="run-stat-value">{runDuration}</div>
                <div className="run-stat-label">Duration</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ─── Live Log ─── */}
      <LiveLog events={events} />
    </main>
  );
}
