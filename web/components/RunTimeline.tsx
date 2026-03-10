'use client';

import { useMemo } from 'react';

export default function RunTimeline({ events }: { events: Array<{ id: number; event_type: string; payload: any; created_at: string }> }) {
  const rows = useMemo(() => [...events].sort((a, b) => a.id - b.id), [events]);

  return (
    <div className="card">
      <h3>Run Timeline</h3>
      <div style={{ maxHeight: 300, overflow: 'auto' }}>
        {rows.map((row) => (
          <div key={row.id} style={{ borderBottom: '1px solid #243455', padding: '8px 0' }}>
            <div><strong>{row.event_type}</strong></div>
            <div className="small">{new Date(row.created_at).toLocaleString()}</div>
            <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(row.payload, null, 2)}</pre>
          </div>
        ))}
        {!rows.length ? <p className="small">No events yet.</p> : null}
      </div>
    </div>
  );
}
