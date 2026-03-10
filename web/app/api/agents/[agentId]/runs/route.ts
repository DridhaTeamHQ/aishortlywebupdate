import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../../../lib/supabase-server';

export async function POST(
    request: Request,
    { params }: { params: { agentId: string } }
) {
    try {
        const user = await getUserFromRequest(request);
        if (!user) {
            return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
        }

        const sb = getServiceClient();

        // Verify agent exists
        const { data: agent } = await sb
            .from('agents')
            .select('id, org_id')
            .eq('id', params.agentId)
            .single();

        if (!agent) {
            return NextResponse.json({ error: 'Agent not found' }, { status: 404 });
        }

        // Create the run
        const { data: run, error } = await sb
            .from('agent_runs')
            .insert({
                org_id: agent.org_id,
                agent_id: agent.id,
                created_by: user.id,
                status: 'queued',
            })
            .select('id, status')
            .single();

        if (error) {
            return NextResponse.json({ error: error.message }, { status: 500 });
        }

        return NextResponse.json({ run_id: run.id, status: run.status });
    } catch (err: any) {
        return NextResponse.json({ error: err.message }, { status: 500 });
    }
}
