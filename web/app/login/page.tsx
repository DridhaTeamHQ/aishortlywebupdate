'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '../../lib/supabase';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [isSignUp, setIsSignUp] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');

    if (isSignUp) {
      const { error } = await supabase.auth.signUp({ email, password });
      if (error) {
        setError(error.message);
        setBusy(false);
        return;
      }
      setError('');
      setIsSignUp(false);
      setBusy(false);
      alert('Check your email to confirm your account, then sign in.');
      return;
    }

    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      setError(error.message);
      setBusy(false);
      return;
    }
    router.push('/dashboard');
  };

  return (
    <div className="login-page">
      <form onSubmit={onSubmit} className="card card-glow login-card">
        <div className="login-header">
          <div className="login-logo">⚡</div>
          <div className="login-title">Shortly AI</div>
          <div className="login-subtitle">Agent Control Center</div>
        </div>

        <div className="form-group">
          <label>Email Address</label>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            autoFocus
          />
        </div>

        <div className="form-group">
          <label>Password</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
          />
        </div>

        <button className="btn btn-primary" disabled={busy} style={{ width: '100%', marginTop: 8, padding: '12px 20px' }}>
          {busy ? (
            <><div className="spinner" /> {isSignUp ? 'Creating account...' : 'Signing in...'}</>
          ) : (
            isSignUp ? 'Create Account' : 'Sign In'
          )}
        </button>

        {error && <div className="form-error">{error}</div>}

        <div style={{ textAlign: 'center', marginTop: 20 }}>
          <button
            type="button"
            onClick={() => { setIsSignUp(!isSignUp); setError(''); }}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--accent)',
              cursor: 'pointer',
              fontSize: 13,
              fontFamily: 'var(--font)',
            }}
          >
            {isSignUp ? 'Already have an account? Sign in' : "Don't have an account? Sign up"}
          </button>
        </div>
      </form>
    </div>
  );
}
