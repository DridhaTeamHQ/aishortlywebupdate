import { NextResponse } from 'next/server';
import { getServiceClient, resolveActor } from '../../../lib/supabase-server';

export async function GET() {
  try {
    const actor = await resolveActor();
    if (!actor) {
      return NextResponse.json({ error: 'No org/profile configured' }, { status: 500 });
    }

    const sb = getServiceClient();
    const { data: agents, error } = await sb
      .from('agents')
      .select('id, slug, display_name, enabled, health_status, created_at')
      .eq('org_id', actor.orgId)
      .order('created_at', { ascending: true });

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    return NextResponse.json({ agents: agents || [] });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || 'Unable to load agents' },
      { status: 500 },
    );
  }
}
