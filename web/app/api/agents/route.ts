import { NextResponse } from 'next/server';
import { getServiceClient, getUserFromRequest } from '../../../lib/supabase-server';

export async function GET(request: Request) {
  try {
    const user = await getUserFromRequest(request);
    if (!user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const sb = getServiceClient();
    const { data: profile } = await sb
      .from('profiles')
      .select('org_id')
      .eq('id', user.id)
      .single();

    if (!profile) {
      return NextResponse.json({ error: 'Profile not found' }, { status: 404 });
    }

    const { data: agents, error } = await sb
      .from('agents')
      .select('id, slug, display_name, enabled, health_status, created_at')
      .eq('org_id', profile.org_id)
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
