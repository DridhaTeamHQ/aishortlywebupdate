import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Shortly AI — Agent Control Center',
  description: 'Start, stop, and monitor your AI news agent in real time.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
