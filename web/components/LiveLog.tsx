'use client';

import { useEffect, useRef } from 'react';

type EventRow = {
    id: number;
    event_type: string;
    payload: Record<string, any>;
    created_at: string;
};

const EVENT_CONFIG: Record<string, { icon: string; label: string; color?: string }> = {
    STEP_STARTED: { icon: '🔄', label: 'Started' },
    STEP_DONE: { icon: '✅', label: 'Completed' },
    LOG: { icon: '📋', label: 'Log' },
    ERROR: { icon: '❌', label: 'Error', color: 'var(--danger)' },
    STREAM_READY: { icon: '📡', label: 'Stream Ready' },
    RUN_FINISHED: { icon: '🏁', label: 'Run Finished' },
};

const STEP_LABELS: Record<string, { icon: string; label: string }> = {
    ingest: { icon: '🔍', label: 'Scraping news sources' },
    resolve_events: { icon: '🔗', label: 'Resolving duplicate stories' },
    breaking_classification: { icon: '🔥', label: 'Classifying breaking news' },
    summarize: { icon: '📝', label: 'Summarizing article' },
    telugu: { icon: '🇮🇳', label: 'Translating to Telugu' },
    image: { icon: '🖼️', label: 'Finding best image' },
    publish: { icon: '🚀', label: 'Publishing to CMS' },
};

function formatTime(iso: string): string {
    try {
        const d = new Date(iso);
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return '';
    }
}

function buildMessage(event: EventRow): { icon: string; message: string; detail?: string; color?: string } {
    const { event_type, payload } = event;
    const config = EVENT_CONFIG[event_type] || { icon: '📌', label: event_type };
    const step = payload?.step || '';
    const stepInfo = STEP_LABELS[step];

    if (event_type === 'STEP_STARTED' && stepInfo) {
        return {
            icon: stepInfo.icon,
            message: stepInfo.label,
            detail: payload.url ? truncateUrl(payload.url) : undefined,
        };
    }

    if (event_type === 'STEP_DONE' && stepInfo) {
        const ok = payload.ok !== undefined ? payload.ok : true;
        const extras: string[] = [];
        if (payload.categories) extras.push(`${payload.categories} categories`);
        if (payload.clusters) extras.push(`${payload.clusters} story clusters`);
        if (payload.breaking !== undefined) extras.push(`${payload.breaking} breaking`);

        return {
            icon: ok ? '✅' : '⚠️',
            message: `${stepInfo.label} — ${ok ? 'done' : 'failed'}`,
            detail: extras.length ? extras.join(' · ') : (payload.url ? truncateUrl(payload.url) : undefined),
            color: ok ? undefined : 'var(--warning)',
        };
    }

    if (event_type === 'RUN_FINISHED') {
        const status = payload.status || 'unknown';
        const published = payload.published;
        return {
            icon: status === 'succeeded' ? '🎉' : status === 'cancelled' ? '🛑' : '💥',
            message: status === 'succeeded'
                ? `Run complete${published !== undefined ? ` — ${published} articles published` : ''}`
                : status === 'cancelled'
                    ? 'Run cancelled'
                    : `Run failed${payload.error ? `: ${payload.error}` : ''}`,
            color: status === 'succeeded' ? 'var(--success)' : status === 'cancelled' ? 'var(--warning)' : 'var(--danger)',
        };
    }

    if (event_type === 'ERROR') {
        return {
            icon: '❌',
            message: payload.message || 'An error occurred',
            color: 'var(--danger)',
        };
    }

    return {
        icon: config.icon,
        message: `${config.label}${step ? `: ${step}` : ''}`,
        detail: payload.url ? truncateUrl(payload.url) : undefined,
        color: config.color,
    };
}

function truncateUrl(url: string): string {
    try {
        const u = new URL(url);
        const path = u.pathname.length > 50 ? u.pathname.slice(0, 50) + '…' : u.pathname;
        return `${u.hostname}${path}`;
    } catch {
        return url.length > 60 ? url.slice(0, 60) + '…' : url;
    }
}

export default function LiveLog({ events }: { events: EventRow[] }) {
    const containerRef = useRef<HTMLDivElement>(null);
    const prevLengthRef = useRef(0);

    useEffect(() => {
        if (events.length > prevLengthRef.current && containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
        prevLengthRef.current = events.length;
    }, [events.length]);

    if (!events.length) {
        return (
            <div className="card">
                <h3>📡 Live Activity</h3>
                <div className="empty-state">
                    <div className="empty-state-icon">🤖</div>
                    <div className="empty-state-text">Start an agent to see live activity here</div>
                </div>
            </div>
        );
    }

    return (
        <div className="card card-glow">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <h3 style={{ margin: 0 }}>📡 Live Activity</h3>
                <span className="text-muted">{events.length} events</span>
            </div>
            <div className="live-log" ref={containerRef}>
                {events.map((event, i) => {
                    const { icon, message, detail, color } = buildMessage(event);
                    return (
                        <div
                            key={event.id}
                            className="log-entry"
                            style={{ animationDelay: `${Math.min(i * 0.02, 0.3)}s` }}
                        >
                            <div className="log-icon">{icon}</div>
                            <div className="log-content">
                                <div className="log-message" style={color ? { color } : undefined}>
                                    {message}
                                </div>
                                {detail && <div className="log-detail">{detail}</div>}
                            </div>
                            <div className="log-time">{formatTime(event.created_at)}</div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
