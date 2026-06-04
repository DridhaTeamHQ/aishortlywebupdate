'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '../../lib/supabase';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [isSignUp, setIsSignUp] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');

    if (isSignUp) {
      const { error } = await supabase.auth.signUp({ email, password });
      if (error) {
        setError(error.message);
        setBusy(false);
        return;
      }
      setIsSignUp(false);
      setBusy(false);
      setNotice('Account created. Check your email to confirm, then sign in.');
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
      <div className="aurora" aria-hidden><span /></div>
      <form onSubmit={onSubmit} className="card glass login-card">
        <div className="login-header">
          <div className="login-logo">⚡</div>
          <div className="login-title">SHORTLY AI</div>
          <div className="login-subtitle">▸ PLAYER LOGIN</div>
        </div>

        <div className="login-field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            className="login-input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
            autoFocus
          />
        </div>

        <div className="login-field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            className="login-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
          />
        </div>

        <button className="btn btn-primary" disabled={busy} style={{ marginTop: 6 }}>
          {busy
            ? <><div className="spinner" /> {isSignUp ? 'CREATING…' : 'LOADING…'}</>
            : (isSignUp ? '★ CREATE PLAYER' : '▶ INSERT COIN')}
        </button>

        {error && <div className="form-error" style={{ marginTop: 14 }}>{error}</div>}
        {notice && <div className="login-notice" style={{ marginTop: 14 }}>{notice}</div>}

        <button
          type="button"
          className="login-switch"
          onClick={() => { setIsSignUp(!isSignUp); setError(''); setNotice(''); }}
        >
          {isSignUp ? 'HAVE AN ACCOUNT? SIGN IN' : 'NEW PLAYER? SIGN UP'}
        </button>
      </form>
    </div>
  );
}
