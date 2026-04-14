const FALLBACK_CONTROL_API_URL = 'http://localhost:8000';

function getControlApiBaseUrl(): string {
  const baseUrl =
    process.env.CONTROL_API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    FALLBACK_CONTROL_API_URL;

  return baseUrl.replace(/\/+$/, '');
}

export async function proxyToControlApi(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const baseUrl = getControlApiBaseUrl();
  const targetUrl = `${baseUrl}${path.startsWith('/') ? path : `/${path}`}`;

  const headers = new Headers(init.headers || {});

  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json');
  }

  return fetch(targetUrl, {
    ...init,
    headers,
    cache: 'no-store',
  });
}
