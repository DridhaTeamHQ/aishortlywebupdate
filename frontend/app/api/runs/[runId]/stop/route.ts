import { NextResponse } from 'next/server';
import { getServiceClient } from '../../../../../lib/supabase-server';

export async function POST(
  request: Request,
  { params }: { params: { runId: string } },
) {
  try {
    const sb = getServiceClient();
    const runId = params.runId;
    const { searchParams } = new URL(request.url);
    const forceCancel = searchParams.get('force') === '1';

    const { data: run } = await sb
      .from('agent_runs')
      .select('id, status, created_by, agent_id')
      .eq('id', runId)
      .maybeSingle();

    if (!run) {
      return NextResponse.json({ error: 'Run not found' }, { status: 404 });
    }

    // Already finished — nothing to do.
    if (['succeeded', 'failed', 'cancelled'].includes(run.status)) {
      return NextResponse.json({ ok: true, status: run.status });
    }

    // One click = immediate, final cancellation. The worker watches for the
    // 'cancelled' status and terminates the running child process within ~1s.
    const finishedAt = new Date().toISOString();
    const { error: cancelError } = await sb
      .from('agent_runs')
      .update({
        status: 'cancelled',
        current_step: 'cancelled',
        finished_at: finishedAt,
      })
      .eq('id', runId);

    if (cancelError) {
      return NextResponse.json({ error: cancelError.message }, { status: 500 });
    }

    await sb.from('agent_run_events').insert({
      run_id: runId,
      agent_id: run.agent_id,
      event_type: 'RUN_FINISHED',
      payload: { status: 'cancelled', error: forceCancel ? 'force_cancelled_from_ui' : 'cancelled_from_ui' },
      created_by: run.created_by,
    });

    return NextResponse.json({ ok: true, status: 'cancelled', forced: forceCancel });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to stop run' },
      { status: 500 },
    );
  }
}
