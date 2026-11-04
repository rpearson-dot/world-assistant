'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-supermarket-'));
process.env.HOME = HOME; // the session files go here, not in the real home

const test = require('node:test');
const assert = require('node:assert/strict');
const { makeServer, product } = require('../src/server');
const { Logins, summary } = require('../src/login');

const TOKEN = 'test-token';
const NOW = new Date('2026-11-04T15:00:00Z');

function fakeProvider(id, opts = {}) {
  return {
    name: id,
    searches: [],
    async search(q, { limit }) {
      this.searches.push(q);
      if (opts.fail && q === opts.fail) throw new Error('blocked');
      if (opts.slow && q === opts.slow) return new Promise(() => {});
      return [1, 2, 3].slice(0, limit).map((n) => ({
        product_uid: `${id}-${q}-${n}`, name: `${q} ${n}`, retail_price: { price: n + (id === 'ocado' ? 0.1 : 0) },
        unit_price: { price: n, measure: 'ltr' }, in_stock: true, provider: id, extra: 'dropped',
      }));
    },
    async getDeliverySlots() {
      return [{ slot_id: 's1', date: '2026-11-05', start_time: '08:00', end_time: '09:00', price: 1.5, available: true }];
    },
  };
}

async function serve(t, { providers = {}, secrets = {}, logins } = {}) {
  const made = {};
  const server = makeServer({
    token: TOKEN,
    createProvider: async (id) => (made[id] = providers[id] || fakeProvider(id)),
    secret: (name) => secrets[name] || '',
    logins: logins || new Logins({ launch: async () => { throw new Error('no browser in tests'); }, now: () => NOW }),
    now: () => NOW,
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, p, body, token = TOKEN) => {
    const r = await fetch(base + p, {
      method, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: r.status, body: await r.json() };
  };
  return { call, made };
}

test('every request needs the token', async (t) => {
  const { call } = await serve(t);
  assert.equal((await call('GET', '/health', undefined, 'wrong')).status, 401);
  assert.equal((await call('GET', '/health')).status, 200);
});

test('refuses to start without a token', () => {
  assert.throws(() => makeServer({ token: '', createProvider: async () => ({}) }), /No API token/);
});

test('compare searches each item at each retailer and keeps only the fields World Assistant uses', async (t) => {
  const { call, made } = await serve(t);
  const r = await call('POST', '/compare', { items: [{ key: 'k1', query: 'milk' }, { key: 'k2', query: 'eggs' }], limit: 2 });
  assert.equal(r.status, 200);
  assert.equal(r.body.results.length, 2);
  const milk = r.body.results[0];
  assert.equal(milk.key, 'k1');
  assert.deepEqual(Object.keys(milk.providers).sort(), ['ocado', 'sainsburys']);
  assert.equal(milk.providers.sainsburys.products.length, 2);
  assert.deepEqual(milk.providers.ocado.products[0], {
    id: 'ocado-milk-1', name: 'milk 1', price: 1.1, unit_price: { price: 1, measure: 'ltr' }, size: null, in_stock: true, image_url: null,
  });
  assert.deepEqual(made.sainsburys.searches.sort(), ['eggs', 'milk']);
});

test('one failed search is reported on that item, the rest still come back', async (t) => {
  const { call } = await serve(t, { providers: { ocado: fakeProvider('ocado', { fail: 'eggs' }) } });
  const r = await call('POST', '/compare', { items: [{ key: 'a', query: 'milk' }, { key: 'b', query: 'eggs' }], providers: ['ocado'] });
  assert.equal(r.body.results[0].providers.ocado.error, null);
  assert.equal(r.body.results[1].providers.ocado.error, 'blocked');
  assert.deepEqual(r.body.results[1].providers.ocado.products, []);
  assert.equal(r.body.results[0].providers.sainsburys, undefined); // only the retailers asked for
});

test('compare checks what it is given', async (t) => {
  const { call } = await serve(t);
  assert.equal((await call('POST', '/compare', { items: [] })).status, 400);
  assert.equal((await call('POST', '/compare', { items: [{ query: ' ' }] })).status, 400);
  assert.equal((await call('POST', '/compare', { items: [{ query: 'x' }], providers: ['tesco'] })).status, 400);
  const many = Array.from({ length: 81 }, (_, i) => ({ query: `item ${i}` }));
  assert.equal((await call('POST', '/compare', { items: many })).status, 400);
  assert.equal((await call('GET', '/compare')).status, 405);
});

test('slots are read only and normalised', async (t) => {
  const { call } = await serve(t);
  const r = await call('GET', '/slots?provider=ocado');
  assert.deepEqual(r.body, { provider: 'ocado', slots: [{ id: 's1', date: '2026-11-05', start: '08:00', end: '09:00', price: 1.5, available: true }] });
  assert.equal((await call('GET', '/slots?provider=tesco')).status, 400);
});

test('health says which logins are saved, without any secrets', async (t) => {
  const { call } = await serve(t, { secrets: { ocado_email: 'a@b.c', ocado_password: 'pw' } });
  const r = await call('GET', '/health');
  assert.equal(r.body.providers.ocado.credentials, true);
  assert.equal(r.body.providers.sainsburys.credentials, false);
  assert.equal(r.body.providers.sainsburys.session.saved, false);
  assert.ok(!JSON.stringify(r.body).includes('pw'));
});

test('cookies exported from a browser become the session', async (t) => {
  const { call } = await serve(t);
  const r = await call('POST', '/session/import', { provider: 'sainsburys', cookies: [{ name: 'WC_AUTHENTICATION_1', value: 'v' }] });
  assert.equal(r.status, 200);
  assert.equal(r.body.session.saved, true);
  assert.equal(r.body.session.expires_at, '2026-11-11T15:00:00.000Z'); // seven days on
  const saved = JSON.parse(fs.readFileSync(path.join(HOME, '.sainsburys', 'session.json'), 'utf8'));
  assert.equal(saved.cookies[0].domain, '.sainsburys.co.uk');
  assert.equal((fs.statSync(path.join(HOME, '.sainsburys', 'session.json')).mode & 0o777), 0o600);
  assert.equal((await call('POST', '/session/import', { provider: 'ocado', cookies: [] })).status, 400);
  assert.equal((await call('POST', '/logout', { provider: 'sainsburys' })).body.status, 'logged_out');
  assert.equal((await call('GET', '/health')).body.providers.sainsburys.session.saved, false);
});

// A browser that behaves like a retailer's sign-in. `buttons`: the selectors that exist on the page (none: only Enter
// works). `twoStep`: the password box appears only after the email is submitted. After the password it goes to
// `afterLogin` (Sainsbury's: its /mfa page); after the code, to `afterCode`.
function fakeBrowser({
  afterLogin = 'https://www.sainsburys.co.uk/gol-ui/mfa', afterCode = 'https://www.sainsburys.co.uk/gol-ui/groceries',
  buttons = ['button[type="submit"]', 'button[data-testid="log-in"]', 'button[data-testid="submit-code"]'], twoStep = false,
} = {}) {
  const log = [];
  let url = 'about:blank';
  let passwordShown = !twoStep;
  const isEmail = (sel) => sel.includes('input[type="email"]');
  const isPassword = (sel) => sel.includes('input[type="password"]');
  const isCode = (sel) => sel.includes('#code');
  const submitted = (how) => {
    log.push(['submit', how]);
    if (url.includes('/mfa')) url = afterCode;
    else if (!passwordShown) passwordShown = true;
    else url = afterLogin;
  };
  const visible = (sel) => isEmail(sel) || (isPassword(sel) && passwordShown) || (isCode(sel) && url.includes('/mfa')) || buttons.includes(sel);
  const locator = (sel) => {
    const l = {
      first: () => l,
      isVisible: async () => visible(sel),
      click: async () => { if (!visible(sel)) throw new Error(`no ${sel}`); submitted(sel); },
      fill: async (v) => { if (!visible(sel)) throw new Error(`Timeout waiting for ${sel}`); log.push(['fill', sel, v]); },
      evaluate: async () => false,
    };
    return l;
  };
  const page = {
    goto: async (u) => { url = u; log.push(['goto', u]); },
    waitForTimeout: async () => {},
    waitForSelector: async (sel) => { if (!visible(sel)) throw new Error(`Timeout waiting for ${sel}`); },
    locator,
    getByRole: () => ({ first() { return this; }, isVisible: async () => false, click: async () => {} }),
    evaluate: async () => {},
    press: async (sel) => { if (!visible(sel)) throw new Error(`no ${sel}`); submitted('Enter'); },
    screenshot: async ({ path: p }) => { fs.writeFileSync(p, 'png'); log.push(['screenshot', p]); },
    url: () => url,
  };
  const context = { newPage: async () => page, pages: () => [page], cookies: async () => [{ name: 'WC_AUTHENTICATION_9', value: 'x', domain: '.sainsburys.co.uk' }] };
  const browser = {
    closed: false,
    newContext: async () => context,
    contexts: () => [context],
    close: async () => { browser.closed = true; },
  };
  return { browser, log };
}

test('the Sainsbury\'s login stops for the SMS code, then saves the session', async (t) => {
  const fake = fakeBrowser();
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins, secrets: { sainsburys_email: 'me@x', sainsburys_password: 'pw' } });
  const started = await call('POST', '/login/start', { provider: 'sainsburys' });
  assert.equal(started.body.status, 'code_needed');
  assert.equal((await call('GET', '/health')).body.login.waiting_for_code, 'sainsburys');
  assert.equal(fake.browser.closed, false); // held open for the code
  assert.equal((await call('POST', '/login/code', { code: '12ab' })).status, 400);
  const done = await call('POST', '/login/code', { code: '123456' });
  assert.equal(done.body.status, 'logged_in');
  assert.equal(done.body.session.saved, true);
  assert.ok(fake.log.some(([op, sel, v]) => op === 'fill' && sel.includes('#code') && v === '123456'));
  assert.equal(fake.browser.closed, true);
  assert.equal((await call('GET', '/health')).body.login, null);
  assert.equal((await call('POST', '/login/code', { code: '123456' })).status, 409); // nothing waiting now
});

test('a wrong code says so and lets go of the browser', async (t) => {
  const fake = fakeBrowser({ afterCode: 'https://www.sainsburys.co.uk/gol-ui/mfa' });
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins, secrets: { sainsburys_email: 'me@x', sainsburys_password: 'pw' } });
  await call('POST', '/login/start', { provider: 'sainsburys' });
  const r = await call('POST', '/login/code', { code: '000000' });
  assert.equal(r.status, 401);
  assert.equal(fake.browser.closed, true);
});

test('the email and password can come with the request', async (t) => {
  const fake = fakeBrowser({ afterLogin: 'https://www.ocado.com/' });
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins });
  const r = await call('POST', '/login/start', { provider: 'ocado', email: 'sent@x', password: 'pw2' });
  assert.equal(r.body.status, 'logged_in');
  assert.ok(fake.log.some(([op, , v]) => op === 'fill' && v === 'sent@x'));
});

test('Ocado logs in in one go; no saved password is a clear error', async (t) => {
  const fake = fakeBrowser({ afterLogin: 'https://www.ocado.com/' });
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins, secrets: { ocado_email: 'me@x', ocado_password: 'pw' } });
  const r = await call('POST', '/login/start', { provider: 'ocado' });
  assert.equal(r.body.status, 'logged_in');
  assert.equal(r.body.session.saved_at, NOW.toISOString());
  const none = await call('POST', '/login/start', { provider: 'sainsburys' });
  assert.equal(none.status, 400);
  assert.match(none.body.error, /No Sainsbury's email and password/);
});

test('summary marks an expired Sainsbury\'s session', () => {
  const s = summary('sainsburys', { cookies: [{}], expiresAt: '2026-11-01T00:00:00Z', lastLogin: '2026-10-25T00:00:00Z' }, NOW);
  assert.equal(s.expired, true);
  assert.deepEqual(summary('ocado', null), { saved: false });
});

test('product keeps a missing price as null', () => {
  assert.equal(product({ product_uid: 1, name: 'x', retail_price: {} }).price, null);
});

test('Ocado with no submit button: Enter in the password box signs in', async (t) => {
  const fake = fakeBrowser({ afterLogin: 'https://www.ocado.com/', buttons: [] });
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins });
  const r = await call('POST', '/login/start', { provider: 'ocado', email: 'me@x', password: 'pw' });
  assert.equal(r.body.status, 'logged_in');
  assert.deepEqual(fake.log.filter(([op]) => op === 'submit'), [['submit', 'Enter']]);
});

test('a sign-in that asks for the email first, then the password', async (t) => {
  const fake = fakeBrowser({ afterLogin: 'https://www.ocado.com/', twoStep: true });
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins });
  const r = await call('POST', '/login/start', { provider: 'ocado', email: 'me@x', password: 'pw' });
  assert.equal(r.body.status, 'logged_in');
  assert.equal(fake.log.filter(([op]) => op === 'submit').length, 2);
  assert.ok(fake.log.some(([op, sel, v]) => op === 'fill' && sel.includes('password') && v === 'pw'));
});

test('a failed login keeps a screenshot and says where it is', async (t) => {
  const fake = fakeBrowser({ afterLogin: 'https://www.ocado.com/login?error=1' });
  const logins = new Logins({ launch: async () => fake.browser, now: () => NOW });
  const { call } = await serve(t, { logins });
  const r = await call('POST', '/login/start', { provider: 'ocado', email: 'me@x', password: 'wrong' });
  assert.equal(r.status, 401);
  assert.match(r.body.error, /Still on the login page.*screenshot.*login-debug\/ocado-2026-11-04T15-00-00-000Z\.png/);
  assert.ok(fs.existsSync(path.join(HOME, 'login-debug', 'ocado-2026-11-04T15-00-00-000Z.png')));
  assert.equal(fake.browser.closed, true);
});
