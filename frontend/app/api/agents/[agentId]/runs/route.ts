import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../../../lib/supabase-server';

const QUEUE_STALE_MS = 3 * 60_000;     // 3 min — give Railway worker time to wake up
const STOP_STALE_MS = 30_000;
const RUN_STALE_MS = 15 * 60_000;

type ActiveRun = {
  id: string;
  status: string;
  created_at: string | null;
  started_at: string | null;
};

function toMillis(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

async function recoverStaleRuns(
  sb: ReturnType<typeof getServiceClient>,
  agentId: string,
  userId: string,
) {
  const { data: activeRuns } = await sb
    .from('agent_runs')
    .select('id, status, created_at, started_at')
    .eq('agent_id', agentId)
    .eq('created_by', userId)
    .in('status', ['queued', 'running', 'stopping']);

  const runs = (activeRuns || []) as ActiveRun[];
  if (!runs.length) return;

  const now = Date.now();
  for (const run of runs) {
    const { data: latestEvent } = await sb
      .from('agent_run_events')
      .select('created_at')
      .eq('run_id', run.id)
      .order('id', { ascending: false })
      .limit(1)
      .maybeSingle();

    const activityAt = Math.max(
      toMillis(run.started_at),
      toMillis(run.created_at),
      toMillis((latestEvent as any)?.created_at || null),
    );
    if (activityAt <= 0) continue;

    const staleWindow =
      run.status === 'queued'
        ? QUEUE_STALE_MS
        : run.status === 'stopping'
          ? STOP_STALE_MS
          : RUN_STALE_MS;

    if ((now - activityAt) < staleWindow) continue;

    const finishedAt = new Date().toISOString();
    await sb
      .from('agent_runs')
      .update({
        status: 'cancelled',
        current_step: 'cancelled',
        finished_at: finishedAt,
      })
      .eq('id', run.id)
      .eq('created_by', userId)
      .in('status', ['queued', 'running', 'stopping']);

    await sb.from('agent_run_events').insert({
      run_id: run.id,
      agent_id: agentId,
      event_type: 'RUN_FINISHED',
      payload: { status: 'cancelled', error: 'auto_cancelled_stale_run' },
      created_by: userId,
    });
  }
}

export async function POST(
  request: Request,
  { params }: { params: { agentId: string } },
) {
  try {
    const user = await getUserFromRequest(request);
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const sb = getServiceClient();
    const agentId = params.agentId;

    // Map frontend labels → internal pipeline keys
    const CATEGORY_ALIASES: Record<string, string> = {
      all: 'all',
      international: 'international',
      national: 'national',
      politics: 'politics',
      sports: 'sports',
      tech: 'tech',
      technology: 'tech',
      business: 'business',
      finance: 'business',
      'finance & business': 'business',
    };
    let category = 'all';
    try {
      const body = await request.json();
      const raw = String(body?.category || '').trim().toLowerCase();
      if (raw in CATEGORY_ALIASES) category = CATEGORY_ALIASES[raw];
    } catch {
      // No body or invalid JSON — default to 'all'
    }

    const { data: profile } = await sb
      .from('profiles')
      .select('org_id')
      .eq('id', user.id)
      .single();

    if (!profile) {
      return NextResponse.json({ error: 'Profile not found' }, { status: 404 });
    }

    const { data: agent } = await sb
      .from('agents')
      .select('id, enabled')
      .eq('id', agentId)
      .eq('org_id', profile.org_id)
      .single();

    if (!agent) {
      return NextResponse.json({ error: 'Agent not found' }, { status: 404 });
    }

    if (!agent.enabled) {
      return NextResponse.json({ error: 'Agent is disabled' }, { status: 400 });
    }

    // Recover stale runs so stop/restart cycles don't remain blocked by old rows.
    await recoverStaleRuns(sb, agentId, user.id);

    const { data: activeRuns } = await sb
      .from('agent_runs')
      .select('id, status')
      .eq('agent_id', agentId)
      .eq('created_by', user.id)
      .in('status', ['queued', 'running', 'stopping'])
      .limit(1);

    if (activeRuns && activeRuns.length > 0) {
      return NextResponse.json(
        { error: 'An agent run is already active.', run_id: activeRuns[0].id, status: activeRuns[0].status },
        { status: 409 },
      );
    }

    const { data: newRun, error } = await sb
      .from('agent_runs')
      .insert({
        org_id: profile.org_id,
        agent_id: agentId,
        created_by: user.id,
        status: 'queued',
        current_step: 'queued',
        category,
      })
      .select('id, status')
      .single();

    if (error || !newRun) {
      return NextResponse.json({ error: error?.message || 'Failed to create run' }, { status: 500 });
    }

    return NextResponse.json({ run_id: newRun.id, status: newRun.status });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to start run' },
      { status: 500 },
    );
  }
}
