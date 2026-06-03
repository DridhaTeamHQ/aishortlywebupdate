import { createClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://placeholder.supabase.co';
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || 'placeholder';

export function getServiceClient() {
    return createClient(url, serviceRoleKey, {
        auth: { persistSession: false, autoRefreshToken: false },
    });
}

export type Actor = { userId: string; orgId: string };

let cachedActor: Actor | null = null;

/**
 * Resolve the default actor (user + org) for this single-tenant deployment.
 * Auth has been removed from the UI, so the API derives a stable identity
 * from the database (or DEFAULT_ORG_ID / DEFAULT_USER_ID env overrides).
 * The service-role client bypasses RLS, so no end-user session is required.
 */
export async function resolveActor(): Promise<Actor | null> {
    if (cachedActor) return cachedActor;

    const sb = getServiceClient();
    let orgId = (process.env.DEFAULT_ORG_ID || '').trim() || null;
    let userId = (process.env.DEFAULT_USER_ID || '').trim() || null;

    try {
        if (!orgId) {
            const { data } = await sb.from('agents').select('org_id').limit(1).maybeSingle();
            orgId = (data as any)?.org_id ?? null;
        }
        if (!userId && orgId) {
            const { data } = await sb
                .from('profiles')
                .select('id')
                .eq('org_id', orgId)
                .limit(1)
                .maybeSingle();
            userId = (data as any)?.id ?? null;
        }
        if (!userId) {
            const { data } = await sb.from('profiles').select('id, org_id').limit(1).maybeSingle();
            userId = (data as any)?.id ?? null;
            if (!orgId) orgId = (data as any)?.org_id ?? null;
        }
    } catch {
        // fall through — handled below
    }

    if (!userId || !orgId) return null;
    cachedActor = { userId, orgId };
    return cachedActor;
}
