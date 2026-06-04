import { createClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://placeholder.supabase.co';
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || 'placeholder';

export function getServiceClient() {
    return createClient(url, serviceRoleKey, {
        auth: { persistSession: false, autoRefreshToken: false },
    });
}

/** Validate the caller's Supabase access token and return the user, or null. */
export async function getUserFromRequest(request: Request): Promise<{ id: string; email: string } | null> {
    const auth = request.headers.get('Authorization');
    if (!auth?.startsWith('Bearer ')) return null;

    const token = auth.slice(7);
    const client = createClient(url, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '');
    const { data, error } = await client.auth.getUser(token);
    if (error || !data.user) return null;

    return { id: data.user.id, email: data.user.email || '' };
}

export type Actor = { userId: string; orgId: string };

/**
 * Resolve the authenticated actor (user + org) from the request's Bearer token.
 * Returns null if the token is missing/invalid or the user has no profile —
 * callers should respond 401 in that case.
 */
export async function getActorFromRequest(request: Request): Promise<Actor | null> {
    const user = await getUserFromRequest(request);
    if (!user) return null;

    const sb = getServiceClient();
    const { data: profile } = await sb
        .from('profiles')
        .select('org_id')
        .eq('id', user.id)
        .single();

    if (!profile) return null;
    return { userId: user.id, orgId: (profile as any).org_id };
}
