'use strict';
// Logins done in a headless browser, in steps a web page can drive.
//
// Sainsbury's sends an SMS code on every new login, so its login is two calls: start() fills in the email and
// password and stops at the code prompt with the browser held open; code() types the code and saves the session.
// Ocado has no code, so start() finishes in one go. The sessions are written where open-supermarkets reads them
// (~/.sainsburys/session.json, ~/.ocado/session.json), so its providers pick them up.
//
// The selectors are open-supermarkets' own (src/auth/login.ts and src/providers/ocado.ts at v3.0.0).

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
const HOLD_MS = 5 * 60 * 1000; // how long a browser waits at the code prompt
const SAINSBURYS_DAYS = 7; // what open-supermarkets assumes a Sainsbury's session lasts

function sessionFile(provider) {
  return path.join(os.homedir(), provider === 'ocado' ? '.ocado' : '.sainsburys', 'session.json');
}

function writeSession(provider, cookies, now = new Date()) {
  const file = sessionFile(provider);
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const data = provider === 'ocado'
    ? { cookies, savedAt: now.toISOString() }
    : { cookies, expiresAt: new Date(now.getTime() + SAINSBURYS_DAYS * 864e5).toISOString(), lastLogin: now.toISOString() };
  fs.writeFileSync(file, JSON.stringify(data, null, 2), { mode: 0o600 });
  return data;
}

function readSession(provider) {
  try {
    return JSON.parse(fs.readFileSync(sessionFile(provider), 'utf8'));
  } catch {
    return null;
  }
}

/** Cookies from a browser export: Playwright storage state, a bare array, or Cookie-Editor's format. */
function normaliseCookies(raw, domain) {
  let list = Array.isArray(raw) ? raw : Array.isArray(raw?.cookies) ? raw.cookies : [];
  list = list
    .filter((c) => c && (c.name || c.Name) && (c.value !== undefined || c.Value !== undefined))
    .map((c) => ({
      name: c.name ?? c.Name,
      value: String(c.value ?? c.Value),
      domain: c.domain ?? c.Domain ?? domain,
      path: c.path ?? c.Path ?? '/',
      expires: c.expirationDate ?? c.expires ?? -1,
      httpOnly: Boolean(c.httpOnly ?? c.HttpOnly ?? false),
      secure: Boolean(c.secure ?? c.Secure ?? false),
      sameSite: ['Strict', 'Lax', 'None'].includes(c.sameSite) ? c.sameSite : 'Lax',
    }));
  if (!list.length) throw Object.assign(new Error('No cookies found in that export.'), { statusCode: 400 });
  return list;
}

async function dropCookieBanner(page) {
  try {
    const accept = page.locator('#onetrust-accept-btn-handler');
    if (await accept.isVisible({ timeout: 3000 })) {
      await accept.click();
      await page.waitForTimeout(1500);
    }
  } catch { /* no banner */ }
  await page.evaluate(() => {
    for (const s of ['.onetrust-pc-dark-filter', '#onetrust-consent-sdk']) document.querySelector(s)?.remove();
  }).catch(() => {});
}

const EMAIL = 'input[type="email"], input[name="email"], input[autocomplete="username"], #username, #email';
const PASSWORD = 'input[type="password"], input[name="password"], #password';
const DEBUG_KEEP = 5;

/**
 * Submit the form the page is on. Each retailer's own button first, then any visible button that says what a sign-in
 * button says, then Enter in `field` (which submits nearly any form). Returns how it was submitted. Short waits each:
 * a selector that matches nothing must not use up the whole login.
 */
async function submit(page, preferred, field, words = /^\s*(sign in|log in|login|continue|next|submit|verify)\b/i) {
  for (const sel of preferred) {
    try {
      const b = page.locator(sel).first();
      if (await b.isVisible({ timeout: 1500 })) {
        await b.click({ timeout: 5000 });
        return sel;
      }
    } catch { /* try the next one */ }
  }
  try {
    const b = page.getByRole('button', { name: words }).first();
    if (await b.isVisible({ timeout: 1500 })) {
      await b.click({ timeout: 5000 });
      return 'a button by its words';
    }
  } catch { /* no such button */ }
  const before = page.url();
  await page.press(field, 'Enter', { timeout: 5000 });
  await page.waitForTimeout(3000);
  // A form with no submit button ignores Enter: submit it directly when nothing happened.
  if (page.url() === before) {
    const done = await page.locator(field).first()
      .evaluate((el) => { if (el.form) { el.form.requestSubmit(); return true; } return false; }).catch(() => false);
    if (done) return 'the form';
  }
  return 'Enter';
}

/** A screenshot of a failed login, in ~/login-debug (WA_DATA_DIR/supermarket/login-debug), the last few kept. */
async function keepScreenshot(page, provider, now) {
  try {
    const dir = path.join(os.homedir(), 'login-debug');
    fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
    const name = `${provider}-${now.toISOString().replace(/[:.]/g, '-')}.png`;
    await page.screenshot({ path: path.join(dir, name), fullPage: true, timeout: 10000 });
    const old = fs.readdirSync(dir).filter((f) => f.endsWith('.png')).sort();
    for (const f of old.slice(0, Math.max(0, old.length - DEBUG_KEEP))) fs.rmSync(path.join(dir, f), { force: true });
    return `login-debug/${name}`;
  } catch {
    return null;
  }
}

function withShot(err, shot) {
  if (shot) err.message = `${err.message} (a screenshot of the page is in the supermarket folder: ${shot})`;
  return err;
}

/**
 * One login at a time. `launch` opens a browser (playwright's chromium.launch, or a fake in the tests).
 * State: idle, or waiting for a code (Sainsbury's) with the page held open until HOLD_MS passes.
 */
class Logins {
  constructor({ launch, now = () => new Date(), holdMs = HOLD_MS } = {}) {
    this.launch = launch || (async (opts) => (await import('playwright')).chromium.launch(opts));
    this.now = now;
    this.holdMs = holdMs;
    this.pending = null; // { provider, browser, context, page, until, timer }
  }

  state() {
    return this.pending ? { waiting_for_code: this.pending.provider, until: new Date(this.pending.until).toISOString() } : null;
  }

  async close() {
    const p = this.pending;
    this.pending = null;
    if (p) {
      clearTimeout(p.timer);
      await p.browser.close().catch(() => {});
    }
  }

  async start(provider, email, password) {
    if (!email || !password) {
      throw Object.assign(new Error(`No ${provider === 'ocado' ? 'Ocado' : "Sainsbury's"} email and password saved.`), { statusCode: 400 });
    }
    await this.close();
    const browser = await this.launch({ headless: true, args: ['--disable-blink-features=AutomationControlled'] });
    try {
      const context = await browser.newContext({ userAgent: UA, locale: 'en-GB', timezoneId: 'Europe/London' });
      const page = await context.newPage();
      const url = provider === 'ocado' ? 'https://www.ocado.com/login' : 'https://www.sainsburys.co.uk/gol-ui/oauth/login';
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(3000);
      await dropCookieBanner(page);
      await page.waitForSelector(EMAIL, { timeout: 20000 });
      await page.locator(EMAIL).first().fill(email);
      const buttons = provider === 'ocado'
        ? ['button[type="submit"]', 'button[data-test="login-button"]', 'input[type="submit"]']
        : ['button[data-testid="log-in"]', 'button[type="submit"]', 'input[type="submit"]'];
      // Some sign-ins ask for the email first and show the password on the next screen.
      let pw = page.locator(PASSWORD).first();
      if (!(await pw.isVisible({ timeout: 2000 }).catch(() => false))) {
        await dropCookieBanner(page);
        await submit(page, buttons, EMAIL);
        await page.waitForSelector(PASSWORD, { state: 'visible', timeout: 20000 });
        pw = page.locator(PASSWORD).first();
      }
      await pw.fill(password);
      await dropCookieBanner(page);
      await submit(page, buttons, PASSWORD);
      await page.waitForTimeout(6000);
      const at = page.url();
      if (provider === 'sainsburys' && at.includes('/mfa')) {
        const until = this.now().getTime() + this.holdMs;
        const timer = setTimeout(() => { this.close(); }, this.holdMs);
        timer.unref?.();
        this.pending = { provider, browser, context, page, until, timer };
        return { status: 'code_needed', until: new Date(until).toISOString() };
      }
      if (at.includes('/login')) throw Object.assign(new Error('Still on the login page: check the email and password.'), { statusCode: 401 });
      const saved = writeSession(provider, await context.cookies(), this.now());
      await browser.close();
      return { status: 'logged_in', session: summary(provider, saved, this.now()) };
    } catch (e) {
      if (!this.pending) {
        const pages = (await Promise.resolve(browser.contexts?.() || []).catch(() => [])).flatMap((c) => c.pages?.() || []);
        const shot = pages.length ? await keepScreenshot(pages[pages.length - 1], provider, this.now()) : null;
        await browser.close().catch(() => {});
        throw withShot(e, shot);
      }
      throw e;
    }
  }

  async code(code) {
    const p = this.pending;
    if (!p) throw Object.assign(new Error('No login is waiting for a code. Start the login again.'), { statusCode: 409 });
    if (!/^\d{6}$/.test(String(code || '').trim())) throw Object.assign(new Error('The code is 6 digits.'), { statusCode: 400 });
    try {
      const box = '#code, input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"]';
      await p.page.locator(box).first().fill(String(code).trim());
      await dropCookieBanner(p.page);
      await submit(p.page, ['button[data-testid="submit-code"]', 'button[type="submit"]'], box);
      await p.page.waitForTimeout(6000);
      const at = p.page.url();
      if (at.includes('login') || at.includes('mfa')) {
        throw Object.assign(new Error('Sainsbury\'s did not accept that code. Start the login again for a new one.'), { statusCode: 401 });
      }
      const saved = writeSession(p.provider, await p.context.cookies(), this.now());
      return { status: 'logged_in', session: summary(p.provider, saved, this.now()) };
    } catch (e) {
      throw withShot(e, await keepScreenshot(p.page, p.provider, this.now()));
    } finally {
      await this.close();
    }
  }
}

/** What /health says about a saved session. */
function summary(provider, data, now = new Date()) {
  if (!data || !Array.isArray(data.cookies) || !data.cookies.length) return { saved: false };
  if (provider === 'ocado') return { saved: true, saved_at: data.savedAt || null };
  const expires = data.expiresAt ? new Date(data.expiresAt) : null;
  return {
    saved: true,
    last_login: data.lastLogin || null,
    expires_at: expires ? expires.toISOString() : null,
    expired: expires ? expires <= now : false,
  };
}

module.exports = { Logins, writeSession, readSession, normaliseCookies, summary, sessionFile };
