import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

import next from 'next';

const webDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(webDir, '..');
const port = Number(process.env.PORT || '3000');
const backendPort = Number(process.env.INTERNAL_API_PORT || '8000');

let shuttingDown = false;

function copyHeaders(sourceHeaders) {
  const headers = new Headers();
  for (const [key, value] of Object.entries(sourceHeaders)) {
    if (!value || key.toLowerCase() === 'host') continue;
    if (Array.isArray(value)) {
      for (const item of value) headers.append(key, item);
    } else {
      headers.set(key, value);
    }
  }
  return headers;
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(typeof chunk === 'string' ? Buffer.from(chunk) : chunk);
  }
  return chunks.length ? Buffer.concat(chunks) : undefined;
}

async function proxyToBackend(req, res) {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const targetPath = url.pathname.replace(/^\/control-api/, '') || '/';
  const targetUrl = new URL(`${targetPath}${url.search}`, `http://127.0.0.1:${backendPort}`);
  const headers = copyHeaders(req.headers);
  const method = req.method || 'GET';
  const body = method === 'GET' || method === 'HEAD' ? undefined : await readBody(req);

  const upstream = await fetch(targetUrl, {
    method,
    headers,
    body,
  });

  res.statusCode = upstream.status;
  upstream.headers.forEach((value, key) => {
    if (key.toLowerCase() === 'transfer-encoding') return;
    res.setHeader(key, value);
  });

  if (!upstream.body) {
    res.end();
    return;
  }

  for await (const chunk of upstream.body) {
    res.write(chunk);
  }
  res.end();
}

function startBackend() {
  const child = spawn(
    'python',
    [
      '-m',
      'uvicorn',
      'app.main:app',
      '--app-dir',
      join('platform', 'api'),
      '--host',
      '127.0.0.1',
      '--port',
      String(backendPort),
    ],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
      },
      stdio: 'inherit',
    },
  );

  child.on('exit', (code, signal) => {
    if (shuttingDown) return;
    console.error(`Backend process exited unexpectedly (code=${code}, signal=${signal})`);
    process.exit(code ?? 1);
  });

  return child;
}

async function main() {
  const backend = startBackend();
  const app = next({ dev: false, dir: webDir });
  const handle = app.getRequestHandler();
  await app.prepare();

  const server = createServer(async (req, res) => {
    try {
      if (req.url?.startsWith('/control-api/')) {
        await proxyToBackend(req, res);
        return;
      }

      await handle(req, res);
    } catch (error) {
      console.error('Request handling failed', error);
      res.statusCode = 500;
      res.setHeader('Content-Type', 'text/plain; charset=utf-8');
      res.end('Internal Server Error');
    }
  });

  const shutdown = () => {
    shuttingDown = true;
    server.close(() => {
      backend.kill('SIGTERM');
      process.exit(0);
    });
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  server.listen(port, '0.0.0.0', () => {
    console.log(`Unified server listening on http://0.0.0.0:${port}`);
    console.log(`Backend proxied at http://127.0.0.1:${backendPort} via /control-api/*`);
  });
}

main().catch((error) => {
  console.error('Failed to start unified server', error);
  process.exit(1);
});
