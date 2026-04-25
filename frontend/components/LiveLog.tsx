'use client';

import { useEffect, useRef } from 'react';

type EventRow = {
    id: number;
    event_type: string;
    payload: Record<string, any>;
    created_at: string;
};

type Kind = 'start' | 'ok' | 'warn' | 'error' | 'info';
type Built = { icon: string; message: string; detail?: string; kind: Kind };

const STEP_META: Record<string, { icon: string; label: string }> = {
    ingest:                  { icon: '📡', label: 'Scraping news sources' },
    resolve_events:          { icon: '🧩', label: 'Resolving duplicate stories' },
    breaking_classification: { icon: '⚡', label: 'Classifying breaking news' },
    summarize:               { icon: '✍️', label: 'Summarizing article' },
    telugu:                  { icon: '🌐', label: 'Translating to Telugu' },
    image:                   { icon: '🖼️', label: 'Finding best image' },
    publish:                 { icon: '🚀', label: 'Publishing to CMS' },
};

function formatTime(iso: string): string {
    try {
        const d = new Date(iso);
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return '';
    }
}

function truncateUrl(url: string): string {
    try {
        const u = new URL(url);
        const path = u.pathname.length > 50 ? `${u.pathname.slice(0, 50)}…` : u.pathname;
        return `${u.hostname}${path}`;
    } catch {
        return url.length > 60 ? `${url.slice(0, 60)}…` : url;
    }
}

function buildMessage(event: EventRow): Built {
    const { event_type, payload } = event;
    const step = payload?.step || '';
    const stepInfo = STEP_META[step];

    if (event_type === 'STEP_STARTED' && stepInfo) {
        return {
            icon: stepInfo.icon,
            message: stepInfo.label,
            detail: payload.url ? truncateUrl(payload.url) : undefined,
            kind: 'start',
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
            message: `${stepInfo.label} ${ok ? '— done' : '— failed'}`,
            detail: extras.length ? extras.join(' · ') : (payload.url ? truncateUrl(payload.url) : undefined),
            kind: ok ? 'ok' : 'warn',
        };
    }

    if (event_type === 'SCRAPE_STARTED') {
        return { icon: '📡', message: 'Scraping configured news sources', kind: 'start' };
    }

    if (event_type === 'SCRAPE_DONE') {
        const total = Number(payload.total || 0);
        return {
            icon: total > 0 ? '📰' : '⚠️',
            message: `Scraping finished — ${total} articles found`,
            kind: total > 0 ? 'ok' : 'warn',
        };
    }

    if (event_type === 'RUN_FINISHED') {
        const status = payload.status || 'unknown';
        const published = payload.published;
        if (status === 'succeeded') {
            return {
                icon: '🎉',
                message: `Run complete${published !== undefined ? ` — ${published} articles published` : ''}`,
                kind: 'ok',
            };
        }
        if (status === 'cancelled') {
            return { icon: '🛑', message: 'Run cancelled', kind: 'warn' };
        }
        return { icon: '❌', message: `Run failed${payload.error ? `: ${payload.error}` : ''}`, kind: 'error' };
    }

    if (event_type === 'ERROR') {
        return { icon: '❌', message: payload.message || 'An error occurred', kind: 'error' };
    }

    if (event_type === 'STREAM_READY') {
        return { icon: '🔌', message: 'Stream ready', kind: 'info' };
    }

    return {
        icon: 'ℹ️',
        message: `${event_type}${step ? `: ${step}` : ''}`,
        detail: payload.message || (payload.url ? truncateUrl(payload.url) : undefined),
        kind: 'info',
    };
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

    const lastIdx = events.length - 1;
    const lastEvent = events[lastIdx];
    const lastIsTerminal = lastEvent && lastEvent.event_type === 'RUN_FINISHED';

    if (!events.length) {
        return (
            <div className="card">
                <h3>Live Activity</h3>
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
                <h3 style={{ margin: 0, display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    Live Activity
                    {!lastIsTerminal && (
                        <span className="typing-dots" aria-hidden>
                            <span /><span /><span />
                        </span>
                    )}
                </h3>
                <span className="text-muted">{events.length} events</span>
            </div>
            <div className="live-log" ref={containerRef}>
                {events.map((event, i) => {
                    const built = buildMessage(event);
                    const isLast = i === lastIdx && !lastIsTerminal;
                    return (
                        <div
                            key={event.id}
                            className="log-entry"
                            data-kind={built.kind}
                            data-active={isLast ? 'true' : 'false'}
                            style={{ animationDelay: `${Math.min(i * 0.02, 0.3)}s` }}
                        >
                            <div className="log-icon">{built.icon}</div>
                            <div className="log-content">
                                <div className="log-message">{built.message}</div>
                                {built.detail && <div className="log-detail">{built.detail}</div>}
                            </div>
                            <div className="log-time">{formatTime(event.created_at)}</div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
