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
      .select('id, status, current_step, created_at, started_at, finished_at, agent_id, created_by')
      .eq('id', params.runId)
      .eq('created_by', user.id)
      .single();

    if (error || !run) {
      return NextResponse.json({ error: 'Run not found' }, { status: 404 });
    }

    return NextResponse.json(run);
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to load run' },
      { status: 500 },
    );
  }
}
