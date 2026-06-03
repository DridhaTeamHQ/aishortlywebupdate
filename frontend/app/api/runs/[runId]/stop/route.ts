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

    if (run.status === 'stopping') {
      if (!forceCancel) {
        return NextResponse.json({ ok: true, status: 'stopping' });
      }

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
        payload: { status: 'cancelled', error: 'force_cancelled_from_ui' },
        created_by: run.created_by,
      });

      return NextResponse.json({ ok: true, status: 'cancelled', forced: true });
    }

    if (run.status === 'queued') {
      const finishedAt = new Date().toISOString();
      const { error: cancelQueuedError } = await sb
        .from('agent_runs')
        .update({
          status: 'cancelled',
          current_step: 'cancelled',
          finished_at: finishedAt,
        })
        .eq('id', runId);

      if (cancelQueuedError) {
        return NextResponse.json({ error: cancelQueuedError.message }, { status: 500 });
      }

      await sb.from('agent_run_events').insert({
        run_id: runId,
        agent_id: run.agent_id,
        event_type: 'RUN_FINISHED',
        payload: { status: 'cancelled', error: 'cancelled_while_queued' },
        created_by: run.created_by,
      });

      return NextResponse.json({ ok: true, status: 'cancelled', forced: false });
    }

    if (!['queued', 'running'].includes(run.status)) {
      return NextResponse.json(
        { error: `Cannot stop a run with status: ${run.status}` },
        { status: 400 },
      );
    }

    const { error } = await sb
      .from('agent_runs')
      .update({ status: 'stopping' })
      .eq('id', runId);

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ ok: true, status: 'stopping', forced: false });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to stop run' },
      { status: 500 },
    );
  }
}
