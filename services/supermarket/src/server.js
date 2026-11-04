'use strict';
// World Assistant's supermarket service: a small HTTP API over open-supermarkets, for World Assistant only.
//
//   GET  /health                      the service, each retailer's saved login, and a login waiting for a code
//   POST /compare                     {items: [{key, query}], providers?, limit?}: search every item at each retailer
//   GET  /slots?provider=             next delivery slots (read only)
//   GET  /basket?provider=            what is in the basket now
//   POST /basket/add                  {provider, items: [{id, name, quantity}]}: each item up to that quantity
//   POST /login/start                 {provider, email, password}: log in (Sainsbury's stops for the SMS code)
//   POST /login/code                  {code, provider}: the code the retailer texted or emailed
//   POST /login/continue              {provider}: after clicking the link the retailer emailed
//   GET  /login/screenshot?provider=  the newest screenshot of a login page (a failure, or the confirmation step)
//   POST /session/import              {provider, cookies}: cookies exported from a real browser instead of a login
//   POST /logout                      {provider}
//
// Every request needs the bearer token (SUPERMARKET_API_TOKEN, or the file named by SUPERMARKET_API_TOKEN_FILE).
// It never books a slot or checks out; it only puts products in the basket when World Assistant asks. It runs on World Assistant's internal network with no port published.

const http = require('node:http');
const fs = require('node:fs');
const { Logins, readSession, writeSession, normaliseCookies, summary, sessionFile, latestScreenshot } = require('./login');

const PROVIDERS = ['sainsburys', 'ocado'];
const LABEL = { sainsburys: "Sainsbury's", ocado: 'Ocado' };
const DOMAIN = { sainsburys: '.sainsburys.co.uk', ocado: '.ocado.com' };
const MAX_ITEMS = 80;
const SEARCH_TIMEOUT_MS = 25000;
const SLOTS_TIMEOUT_MS = 120000;
const PER_PROVIDER = 2; // searches at a time at one retailer: be a polite shopper

function readSecret(name, env = process.env) {
  const file = env[`${name.toUpperCase()}_FILE`] || `/run/secrets/${name}`;
  try {
    const v = fs.readFileSync(file, 'utf8').trim();
    if (v) return v;
  } catch { /* not there */ }
  return (env[name.toUpperCase()] || '').trim();
}

function fail(status, message) {
  return Object.assign(new Error(message), { statusCode: status });
}

function withTimeout(promise, ms, what) {
  let t;
  return Promise.race([
    promise.finally(() => clearTimeout(t)),
    new Promise((_, reject) => { t = setTimeout(() => reject(fail(504, `${what} took longer than ${ms / 1000} s`)), ms); }),
  ]);
}

async function mapLimit(list, limit, fn) {
  const out = new Array(list.length);
  let next = 0;
  async function worker() {
    while (next < list.length) {
      const i = next++;
      out[i] = await fn(list[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, list.length) }, worker));
  return out;
}

/** The fields World Assistant uses, whatever else a provider returns. */
function product(p) {
  const price = Number(p?.retail_price?.price);
  const unit = p?.unit_price && Number.isFinite(Number(p.unit_price.price))
    ? { price: Number(p.unit_price.price), measure: String(p.unit_price.measure || '') } : null;
  return {
    id: String(p.product_uid),
    name: String(p.name || ''),
    price: Number.isFinite(price) ? price : null,
    unit_price: unit,
    size: p.size || null,
    in_stock: p.in_stock === undefined ? null : p.in_stock,
    image_url: p.image_url || null,
  };
}

function makeServer({ token, createProvider, logins = new Logins(), secret = readSecret, now = () => new Date() }) {
  if (!token) throw new Error('No API token: set SUPERMARKET_API_TOKEN or SUPERMARKET_API_TOKEN_FILE.');
  const cache = new Map(); // provider id -> instance; dropped after a login so the new session is read
  const slotsInFlight = new Map();

  async function provider(id) {
    if (!PROVIDERS.includes(id)) throw fail(400, `provider must be one of ${PROVIDERS.join(', ')}`);
    if (!cache.has(id)) cache.set(id, createProvider(id));
    try {
      return await cache.get(id);
    } catch (e) {
      cache.delete(id);
      throw e;
    }
  }

  function credentials(id) {
    return { email: secret(`${id}_email`), password: secret(`${id}_password`) };
  }

  function health() {
    const providers = {};
    for (const id of PROVIDERS) {
      const c = credentials(id);
      providers[id] = { label: LABEL[id], credentials: Boolean(c.email && c.password), session: summary(id, readSession(id), now()) };
    }
    return { ok: true, service: 'world-assistant-supermarket', providers, login: logins.state() };
  }

  async function compare(body) {
    const items = Array.isArray(body?.items) ? body.items : null;
    if (!items || !items.length) throw fail(400, 'items: give at least one {key, query}');
    if (items.length > MAX_ITEMS) throw fail(400, `items: at most ${MAX_ITEMS} at once`);
    for (const it of items) {
      if (!it || typeof it.query !== 'string' || !it.query.trim() || it.query.length > 120) {
        throw fail(400, 'items: each needs a query of 1 to 120 characters');
      }
    }
    const ids = Array.isArray(body.providers) && body.providers.length ? body.providers : PROVIDERS;
    for (const id of ids) if (!PROVIDERS.includes(id)) throw fail(400, `providers: unknown "${id}"`);
    const limit = Math.min(Math.max(parseInt(body.limit, 10) || 5, 1), 10);

    const byProvider = {};
    await Promise.all(ids.map(async (id) => {
      let p;
      try {
        p = await provider(id);
      } catch (e) {
        byProvider[id] = items.map(() => ({ products: [], error: e.message }));
        return;
      }
      byProvider[id] = await mapLimit(items, PER_PROVIDER, async (it) => {
        try {
          const found = await withTimeout(p.search(it.query.trim(), { limit }), SEARCH_TIMEOUT_MS, `${LABEL[id]} search`);
          return { products: (found || []).slice(0, limit).map(product), error: null };
        } catch (e) {
          return { products: [], error: e.message || String(e) };
        }
      });
    }));
    return {
      results: items.map((it, i) => ({
        key: it.key ?? String(i),
        query: it.query,
        providers: Object.fromEntries(ids.map((id) => [id, byProvider[id][i]])),
      })),
    };
  }

  async function slots(id) {
    const p = await provider(id);
    if (typeof p.getDeliverySlots !== 'function') throw fail(501, `${LABEL[id]} has no delivery slots here`);
    if (!slotsInFlight.has(id)) {
      slotsInFlight.set(id, withTimeout(p.getDeliverySlots(), SLOTS_TIMEOUT_MS, `${LABEL[id]} slots`)
        .finally(() => slotsInFlight.delete(id)));
    }
    const list = await slotsInFlight.get(id);
    return {
      provider: id,
      slots: (list || []).map((s) => ({
        id: String(s.slot_id || ''), date: s.date || '', start: s.start_time || '', end: s.end_time || '',
        price: Number.isFinite(Number(s.price)) ? Number(s.price) : null, available: Boolean(s.available),
      })),
    };
  }

  /** The basket as World Assistant shows it. */
  function basketOut(id, b) {
    return {
      provider: id,
      items: (b?.items || []).map((i) => ({ id: String(i.item_id), product_id: String(i.product_uid ?? ''), name: i.name || '',
        quantity: Number(i.quantity) || 0, total: Number(i.total_price) || 0 })),
      total: Number(b?.total_cost) || 0,
      count: Number(b?.total_quantity) || 0,
    };
  }

  async function basket(id) {
    const p = await provider(id);
    if (typeof p.getBasket !== 'function') throw fail(501, `${LABEL[id]} has no basket here`);
    return basketOut(id, await withTimeout(p.getBasket(), 60000, `${LABEL[id]} basket`));
  }

  /**
   * Put the list's products in the basket. Each item reaches the quantity asked for: added when it is not there,
   * raised when there are fewer, left alone when there are already enough (so sending twice never doubles it).
   * One at a time, and one failure does not stop the rest.
   */
  async function addAll(id, items) {
    if (!Array.isArray(items) || !items.length) throw fail(400, 'items: give at least one {id, quantity}');
    if (items.length > MAX_ITEMS) throw fail(400, `items: at most ${MAX_ITEMS} at once`);
    const p = await provider(id);
    if (typeof p.addToBasket !== 'function') throw fail(501, `${LABEL[id]} has no basket here`);
    const before = basketOut(id, await withTimeout(p.getBasket(), 60000, `${LABEL[id]} basket`));
    const norm = (x) => String(x || '').trim().toLowerCase();
    const results = [];
    for (const it of items) {
      const want = Math.min(Math.max(parseInt(it?.quantity, 10) || 1, 1), 99);
      const pid = String(it?.id || '');
      if (!pid) { results.push({ id: pid, name: it?.name || '', status: 'failed', error: 'no product id' }); continue; }
      const have = before.items.find((b) => b.product_id === pid || b.id === pid || (it.name && norm(b.name) === norm(it.name)));
      try {
        if (!have) {
          await withTimeout(p.addToBasket(pid, want), 30000, 'adding to the basket');
          results.push({ id: pid, name: it.name || '', status: 'added', quantity: want });
        } else if (have.quantity < want) {
          await withTimeout(p.updateBasketItem(have.id, want), 30000, 'changing the basket');
          results.push({ id: pid, name: it.name || '', status: 'raised', quantity: want, was: have.quantity });
        } else {
          results.push({ id: pid, name: it.name || '', status: 'already', quantity: have.quantity });
        }
      } catch (e) {
        results.push({ id: pid, name: it.name || '', status: 'failed', error: e.message || String(e) });
      }
    }
    let after = null;
    try { after = basketOut(id, await withTimeout(p.getBasket(), 60000, `${LABEL[id]} basket`)); } catch { /* the results still stand */ }
    return { provider: id, results, basket: after };
  }

  async function route(req, url, body) {
    const p = url.pathname;
    if (req.method === 'GET' && (p === '/' || p === '/health')) return health();
    if (req.method === 'POST' && p === '/compare') return compare(body);
    if (req.method === 'GET' && p === '/slots') return slots(url.searchParams.get('provider') || '');
    if (req.method === 'GET' && p === '/basket') return basket(url.searchParams.get('provider') || '');
    if (req.method === 'POST' && p === '/basket/add') {
      if (!PROVIDERS.includes(body?.provider)) throw fail(400, `provider must be one of ${PROVIDERS.join(', ')}`);
      return addAll(body.provider, body.items);
    }
    if (req.method === 'POST' && p === '/login/start') {
      const id = body?.provider;
      if (!PROVIDERS.includes(id)) throw fail(400, `provider must be one of ${PROVIDERS.join(', ')}`);
      // World Assistant sends the email and password with the request (they stay in its key store); a standalone
      // run can keep them in secrets instead.
      const c = credentials(id);
      const out = await logins.start(id, body.email || c.email, body.password || c.password);
      if (out.status === 'logged_in') cache.delete(id);
      return out;
    }
    if (req.method === 'POST' && p === '/login/code') {
      const out = await logins.code(body?.code, body?.provider);
      cache.clear();
      return out;
    }
    if (req.method === 'POST' && p === '/login/continue') {
      const out = await logins.continue(body?.provider);
      cache.clear();
      return out;
    }
    if (req.method === 'GET' && p === '/login/screenshot') {
      const id = url.searchParams.get('provider') || '';
      if (!PROVIDERS.includes(id)) throw fail(400, `provider must be one of ${PROVIDERS.join(', ')}`);
      const file = latestScreenshot(id);
      if (!file) throw fail(404, 'No screenshot kept for that retailer');
      return { __file: file, __type: 'image/png', __name: require('node:path').basename(file) };
    }
    if (req.method === 'POST' && p === '/session/import') {
      const id = body?.provider;
      if (!PROVIDERS.includes(id)) throw fail(400, `provider must be one of ${PROVIDERS.join(', ')}`);
      const saved = writeSession(id, normaliseCookies(body.cookies, DOMAIN[id]), now());
      cache.delete(id);
      return { status: 'imported', session: summary(id, saved, now()) };
    }
    if (req.method === 'POST' && p === '/logout') {
      const id = body?.provider;
      if (!PROVIDERS.includes(id)) throw fail(400, `provider must be one of ${PROVIDERS.join(', ')}`);
      fs.rmSync(sessionFile(id), { force: true });
      cache.delete(id);
      return { status: 'logged_out' };
    }
    if (['/', '/health', '/compare', '/slots', '/basket', '/basket/add', '/login/start', '/login/code', '/login/continue', '/login/screenshot', '/session/import', '/logout'].includes(p)) {
      throw fail(405, 'Method not allowed');
    }
    throw fail(404, 'Not found');
  }

  return http.createServer((req, res) => {
    const send = (status, data) => {
      res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
      res.end(JSON.stringify(data));
    };
    if (req.headers.authorization !== `Bearer ${token}`) return send(401, { error: 'Unauthorized' });
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > 512 * 1024) { send(413, { error: 'Request too large' }); req.destroy(); } else chunks.push(c);
    });
    req.on('end', async () => {
      if (res.writableEnded) return;
      try {
        let body = null;
        if (chunks.length) {
          try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw fail(400, 'The body is not JSON'); }
        }
        const url = new URL(req.url || '/', 'http://supermarket');
        const out = await route(req, url, body);
        if (out && out.__file) {
          res.writeHead(200, { 'content-type': out.__type, 'cache-control': 'no-store', 'x-screenshot-name': out.__name || '' });
          res.end(fs.readFileSync(out.__file));
          return;
        }
        send(200, out);
      } catch (e) {
        const err = { error: e?.message || 'Failed' };
        if (e?.screenshot) err.screenshot = e.screenshot;
        send(Number.isInteger(e?.statusCode) ? e.statusCode : 502, err);
      }
    });
  });
}

function main() {
  const { createProvider } = require('open-supermarkets/dist/providers/registry');
  const token = readSecret('supermarket_api_token');
  const port = Number(process.env.SUPERMARKET_API_PORT || 7876);
  const host = process.env.SUPERMARKET_API_HOST || '0.0.0.0';
  const server = makeServer({ token, createProvider });
  server.listen(port, host, () => console.log(`supermarket service on ${host}:${port}`));
  const stop = () => server.close(() => process.exit(0));
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

if (require.main === module) main();

module.exports = { makeServer, product, mapLimit, readSecret, PROVIDERS };
