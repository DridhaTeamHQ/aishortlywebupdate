import { createClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://placeholder.supabase.co';
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || 'placeholder';

export function getServiceClient() {
    return createClient(url, serviceRoleKey, {
        auth: { persistSession: false, autoRefreshToken: false },
    });
}

export async function getUserFromRequest(request: Request): Promise<{ id: string; email: string } | null> {
    const auth = request.headers.get('Authorization');
    if (!auth?.startsWith('Bearer ')) return null;

    const token = auth.slice(7);
    const client = createClient(url, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '');
    const { data, error } = await client.auth.getUser(token);
    if (error || !data.user) return null;

    return { id: data.user.id, email: data.user.email || '' };
}
