import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../../../lib/supabase-server';

const ORPHAN_RUN_MAX_AGE_MS = 10 * 60 * 1000;

function toMillis(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export async function GET(
  request: Request,
  { params }: { params: { agentId: string } },
) {
  try {
    const user = await getUserFromRequest(request);
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const sb = getServiceClient();
    const { data: runs } = await sb
      .from('agent_runs')
      .select('id, status, current_step, created_at, started_at, finished_at')
      .eq('agent_id', params.agentId)
      .in('status', ['queued', 'running', 'stopping'])
      .order('created_at', { ascending: false })
      .limit(1);

    const run = (runs || [])[0];
    if (!run) {
      return NextResponse.json({ run: null });
    }

    // Treat very old queued/stopping rows as orphans so the UI doesn't get stuck.
    const createdAtMs = toMillis(run.created_at);
    const isOrphan =
      (run.status === 'queued' || run.status === 'stopping') &&
      createdAtMs > 0 &&
      Date.now() - createdAtMs > ORPHAN_RUN_MAX_AGE_MS;

    if (isOrphan) {
      return NextResponse.json({ run: null });
    }

    return NextResponse.json({ run });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to load active run' },
      { status: 500 },
    );
  }
}
