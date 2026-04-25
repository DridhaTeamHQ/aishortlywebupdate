'use client';

import { useState } from 'react';

export type Agent = {
  id: string;
  slug: string;
  display_name: string;
  enabled: boolean;
  health_status: string;
};

type Props = {
  agent: Agent;
  isRunning: boolean;
  onStartRun: (category: string) => Promise<void>;
  onStopRun: () => Promise<void>;
  runStatus?: string;
  currentStep?: string;
};

const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: '🗞️  All Categories' },
  { value: 'international', label: '🌍  International' },
  { value: 'national', label: '🇮🇳  National' },
  { value: 'politics', label: '🏛️  Politics' },
  { value: 'business', label: '💹  Finance & Business' },
  { value: 'tech', label: '💻  Technology' },
  { value: 'sports', label: '🏆  Sports' },
];

export default function AgentCard({ agent, isRunning, onStartRun, onStopRun, runStatus, currentStep }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [category, setCategory] = useState<string>('all');

  const handleStart = async () => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await onStartRun(category);
    } catch (e: any) {
      setError(e.message || 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    if (busy || runStatus === 'stopping') return;
    setBusy(true);
    setError('');
    try {
      await onStopRun();
    } catch (e: any) {
      setError(e.message || 'Failed');
    } finally {
      setBusy(false);
    }
  };

  const statusDotClass = runStatus
    ? `status-dot status-dot-${runStatus}`
    : `status-dot status-dot-${agent.health_status === 'healthy' ? 'succeeded' : 'failed'}`;

  return (
    <div className="card agent-card">
      <div className="agent-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className={statusDotClass} />
          <span className="agent-name">{agent.display_name}</span>
        </div>
        {runStatus && (
          <span className={`badge badge-${runStatus}`}>
            {runStatus}
          </span>
        )}
      </div>

      <div className="agent-meta">
        <div className="agent-meta-item">
          <span>Tag</span>
          <span>{agent.slug}</span>
        </div>
        {currentStep && (
          <div className="agent-meta-item">
            <span>Step</span>
            <span>{currentStep}</span>
          </div>
        )}
      </div>

      {!isRunning && (
        <div style={{ marginBottom: 10 }}>
          <label
            htmlFor={`cat-${agent.id}`}
            style={{ display: 'block', fontSize: 12, color: '#9ca3af', marginBottom: 6 }}
          >
            News Category
          </label>
          <select
            id={`cat-${agent.id}`}
            className="category-select"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            disabled={busy || !agent.enabled}
          >
            {CATEGORY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        {!isRunning ? (
          <button
            className="btn btn-primary"
            onClick={handleStart}
            disabled={busy || !agent.enabled}
          >
            {busy ? <><div className="spinner" /> Starting…</> : <>▶  Start Agent</>}
          </button>
        ) : (
          <button
            className="btn btn-danger"
            onClick={handleStop}
            disabled={busy || runStatus === 'stopping'}
          >
            {busy || runStatus === 'stopping' ? <><div className="spinner" /> Stopping…</> : <>■  Stop Agent</>}
          </button>
        )}
      </div>

      {error && <div className="form-error" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  );
}
