import { NextResponse } from 'next/server';
import { getServiceClient } from '../../../../lib/supabase-server';

export async function GET(
    _request: Request,
    { params }: { params: { runId: string } }
) {
    try {
        const sb = getServiceClient();
        const { data: run, error } = await sb
            .from('agent_runs')
            .select('id, status, current_step, stream_url, created_at, started_at, finished_at')
            .eq('id', params.runId)
            .single();

        if (error || !run) {
            return NextResponse.json({ error: 'Run not found' }, { status: 404 });
        }

        return NextResponse.json({ run });
    } catch (err: any) {
        return NextResponse.json({ error: err.message }, { status: 500 });
    }
}
