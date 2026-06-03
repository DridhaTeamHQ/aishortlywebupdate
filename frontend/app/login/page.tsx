import { redirect } from 'next/navigation';

// Authentication has been removed — the dashboard is open. Any old /login link
// simply forwards to the dashboard.
export default function LoginPage() {
  redirect('/dashboard');
}
