import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../../../lib/supabase-server';

export async function POST(
    request: Request,
    { params }: { params: { runId: string } }
) {
    try {
        const user = await getUserFromRequest(request);
        if (!user) {
            return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
        }

        const sb = getServiceClient();

        // Only running runs can be stopped
        const { data: run } = await sb
            .from('agent_runs')
            .select('id, status')
            .eq('id', params.runId)
            .in('status', ['queued', 'running'])
            .single();

        if (!run) {
            return NextResponse.json({ error: 'Run not stoppable or not found' }, { status: 409 });
        }

        const { error } = await sb
            .from('agent_runs')
            .update({ status: 'stopping' })
            .eq('id', params.runId);

        if (error) {
            return NextResponse.json({ error: error.message }, { status: 500 });
        }

        return NextResponse.json({ run_id: params.runId, status: 'stopping' });
    } catch (err: any) {
        return NextResponse.json({ error: err.message }, { status: 500 });
    }
}
