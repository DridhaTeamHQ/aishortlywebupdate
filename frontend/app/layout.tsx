import type { Metadata } from 'next';
import './globals.css';

const faviconSvg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
  <defs>
    <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0%' stop-color='%2322d3ee'/>
      <stop offset='55%' stop-color='%23a855f7'/>
      <stop offset='100%' stop-color='%23ec4899'/>
    </linearGradient>
  </defs>
  <rect width='64' height='64' rx='14' fill='url(%23g)'/>
  <path d='M36 10 L18 36 H30 L26 54 L46 26 H34 Z' fill='%23fff'/>
</svg>`.replace(/\s+/g, ' ').trim();

export const metadata: Metadata = {
  title: 'Shortly AI — Agent Control Center',
  description: 'Start, stop, and monitor your AI news agent in real time.',
  icons: {
    icon: [
      { url: `data:image/svg+xml;utf8,${faviconSvg}`, type: 'image/svg+xml' },
    ],
  },
};

// Apply the saved theme before first paint to avoid a flash of the wrong mode.
const themeScript = `(function(){try{var t=localStorage.getItem('shortly-theme');if(t!=='light'&&t!=='dark'){t=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','light');}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
