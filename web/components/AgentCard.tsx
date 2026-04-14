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
  onStartRun: () => Promise<void>;
  onStopRun: () => Promise<void>;
  runStatus?: string;
  currentStep?: string;
};

export default function AgentCard({ agent, isRunning, onStartRun, onStopRun, runStatus, currentStep }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const handleStart = async () => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await onStartRun();
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

      <div style={{ display: 'flex', gap: 8 }}>
        {!isRunning ? (
          <button
            className="btn btn-primary"
            onClick={handleStart}
            disabled={busy || !agent.enabled}
          >
            {busy ? <><div className="spinner" /> Starting...</> : 'Start Agent'}
          </button>
        ) : (
          <button
            className="btn btn-danger"
            onClick={handleStop}
            disabled={busy || runStatus === 'stopping'}
          >
            {busy || runStatus === 'stopping' ? <><div className="spinner" /> Stopping...</> : 'Stop Agent'}
          </button>
        )}
      </div>

      {error && <div className="form-error" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  );
}
