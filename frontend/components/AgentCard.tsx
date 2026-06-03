'use client';

import { useState } from 'react';
import CategoryDropdown, { CategoryOption } from './CategoryDropdown';

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

const CATEGORY_OPTIONS: CategoryOption[] = [
  { value: 'all',           icon: '🗞️', label: 'All Categories' },
  { value: 'international', icon: '🌍', label: 'International' },
  { value: 'national',      icon: '🇮🇳', label: 'National' },
  { value: 'politics',      icon: '🏛️', label: 'Politics' },
  { value: 'business',      icon: '💹', label: 'Finance & Business' },
  { value: 'tech',          icon: '💻', label: 'Technology' },
  { value: 'sports',        icon: '🏆', label: 'Sports' },
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
    <div className="card glass agent-card">
      <div className="agent-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
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
          <span className="meta-key">Tag</span>
          <span className="meta-val">{agent.slug}</span>
        </div>
        {currentStep && (
          <div className="agent-meta-item">
            <span className="meta-key">Step</span>
            <span className="meta-val">{currentStep}</span>
          </div>
        )}
      </div>

      {!isRunning && (
        <div style={{ marginBottom: 14 }}>
          <label htmlFor={`cat-${agent.id}`}>News Category</label>
          <CategoryDropdown
            id={`cat-${agent.id}`}
            options={CATEGORY_OPTIONS}
            value={category}
            onChange={setCategory}
            disabled={busy || !agent.enabled}
          />
        </div>
      )}

      {!isRunning ? (
        <button className="btn btn-primary" onClick={handleStart} disabled={busy || !agent.enabled}>
          {busy ? <><div className="spinner" /> Starting…</> : <>▶&nbsp;&nbsp;Start Agent</>}
        </button>
      ) : (
        <button className="btn btn-danger" onClick={handleStop} disabled={busy || runStatus === 'stopping'}>
          {busy || runStatus === 'stopping' ? <><div className="spinner" /> Stopping…</> : <>■&nbsp;&nbsp;Stop Agent</>}
        </button>
      )}

      {error && <div className="form-error" style={{ marginTop: 12 }}>{error}</div>}
    </div>
  );
}
