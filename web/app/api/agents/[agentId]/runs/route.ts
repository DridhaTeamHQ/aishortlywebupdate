import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../../../lib/supabase-server';

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
