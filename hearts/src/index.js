// placingstones hearts: a tiny reaction counter for the journal.
//
//   GET  /:slug?c=<client>   -> { count, hearted }
//   POST /:slug  { client }  -> toggles the heart, returns { count, hearted }
//
// A client is a random id the browser keeps in localStorage. Nothing else is
// stored about the reader except a salted hash of their IP, used only to cap
// how many distinct ids one address can heart a post with.

const ALLOWED_ORIGINS = new Set([
  'https://placingstones.dev',
  'https://www.placingstones.dev',
  'http://localhost:4321',
]);

const SLUG = /^[a-z0-9][a-z0-9-]{0,79}$/;
const CLIENT = /^[A-Za-z0-9_-]{16,64}$/;
const PER_IP_CAP = 20;

function cors(request) {
  const origin = request.headers.get('Origin') ?? '';
  const headers = {
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
  if (ALLOWED_ORIGINS.has(origin)) headers['Access-Control-Allow-Origin'] = origin;
  return headers;
}

function json(body, status, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...extra },
  });
}

async function hashIp(ip, salt) {
  const data = new TextEncoder().encode(`${salt}:${ip}`);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].slice(0, 16).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function count(db, slug) {
  const row = await db
    .prepare(
      `SELECT (SELECT COUNT(*) FROM hearts WHERE slug = ?1)
            + COALESCE((SELECT count FROM offsets WHERE slug = ?1), 0) AS n`
    )
    .bind(slug)
    .first();
  return row?.n ?? 0;
}

async function hearted(db, slug, client) {
  if (!client) return false;
  const row = await db.prepare('SELECT 1 FROM hearts WHERE slug = ? AND client = ?').bind(slug, client).first();
  return !!row;
}

export default {
  async fetch(request, env) {
    const headers = cors(request);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });

    const url = new URL(request.url);
    const slug = url.pathname.replace(/^\/+|\/+$/g, '');
    if (!SLUG.test(slug)) return json({ error: 'bad slug' }, 404, headers);

    if (request.method === 'GET') {
      const client = url.searchParams.get('c') ?? '';
      const [n, h] = await Promise.all([count(env.DB, slug), hearted(env.DB, slug, CLIENT.test(client) ? client : '')]);
      return json({ count: n, hearted: h }, 200, headers);
    }

    if (request.method === 'POST') {
      if (!headers['Access-Control-Allow-Origin']) return json({ error: 'origin not allowed' }, 403, headers);
      let client;
      try {
        ({ client } = await request.json());
      } catch {
        return json({ error: 'bad body' }, 400, headers);
      }
      if (!CLIENT.test(client ?? '')) return json({ error: 'bad client' }, 400, headers);

      const ip = request.headers.get('CF-Connecting-IP') ?? '0.0.0.0';
      const ipHash = await hashIp(ip, env.IP_SALT ?? 'placingstones');

      const existing = await env.DB.prepare('SELECT 1 FROM hearts WHERE slug = ? AND client = ?').bind(slug, client).first();
      let isHearted;
      if (existing) {
        await env.DB.prepare('DELETE FROM hearts WHERE slug = ? AND client = ?').bind(slug, client).run();
        isHearted = false;
      } else {
        const fromIp = await env.DB
          .prepare('SELECT COUNT(*) AS n FROM hearts WHERE slug = ? AND ip_hash = ?')
          .bind(slug, ipHash)
          .first();
        if ((fromIp?.n ?? 0) >= PER_IP_CAP) return json({ error: 'too many' }, 429, headers);
        await env.DB
          .prepare('INSERT OR IGNORE INTO hearts (slug, client, ip_hash) VALUES (?, ?, ?)')
          .bind(slug, client, ipHash)
          .run();
        isHearted = true;
      }
      return json({ count: await count(env.DB, slug), hearted: isHearted }, 200, headers);
    }

    return json({ error: 'method not allowed' }, 405, headers);
  },
};
