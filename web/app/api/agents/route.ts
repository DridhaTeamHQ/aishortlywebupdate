import { NextResponse } from 'next/server';
import { getServiceClient } from '../../../lib/supabase-server';

export async function GET() {
    try {
        const sb = getServiceClient();
        const { data, error } = await sb
            .from('agents')
            .select('id, slug, display_name, enabled, health_status')
            .order('created_at', { ascending: true });

        if (error) {
            return NextResponse.json({ error: error.message }, { status: 500 });
        }

        return NextResponse.json({ agents: data || [] });
    } catch (err: any) {
        return NextResponse.json({ error: err.message }, { status: 500 });
    }
}
