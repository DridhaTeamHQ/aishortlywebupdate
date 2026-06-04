import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../../lib/supabase-server';

export async function GET(
  request: Request,
  { params }: { params: { runId: string } },
) {
  try {
    const user = await getUserFromRequest(request);
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const sb = getServiceClient();
    const { data: run, error } = await sb
      .from('agent_runs')
      .select('id, status, current_step, created_at, started_at, finished_at, agent_id, created_by, category')
      .eq('id', params.runId)
      .maybeSingle();

    if (error || !run) {
      return NextResponse.json({ error: 'Run not found' }, { status: 404 });
    }

    // Return events alongside the run so the dashboard polls a single endpoint.
    const { searchParams } = new URL(request.url);
    let events: any[] = [];
    if (searchParams.get('events') !== '0') {
      const { data } = await sb
        .from('agent_run_events')
        .select('id, event_type, payload, created_at')
        .eq('run_id', params.runId)
        .order('id', { ascending: true });
      events = data || [];
    }

    return NextResponse.json({ run, events });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to load run' },
      { status: 500 },
    );
  }
}
