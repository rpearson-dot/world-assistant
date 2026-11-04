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
  if (shot) {
    err.screenshot = shot;
    err.message = `${err.message} (the Shopping page shows a screenshot of what the retailer's page said)`;
  }
  return err;
}

/** The newest screenshot kept for a provider: its file path, or null. */
function latestScreenshot(provider) {
  try {
    const dir = path.join(os.homedir(), 'login-debug');
    const files = fs.readdirSync(dir).filter((f) => f.startsWith(`${provider}-`) && f.endsWith('.png')).sort();
    return files.length ? path.join(dir, files[files.length - 1]) : null;
  } catch {
    return null;
  }
}

const LABELS = { ocado: 'Ocado', sainsburys: "Sainsbury's" };
const CODE_BOX = '#code, input[name="code"], input[name*="otp" i], input[autocomplete="one-time-code"], input[inputmode="numeric"]';
const CHECK_WORDS = /verif|confirm (your|it'?s you)|check your (email|inbox|phone)|we('ve| have) (sent|emailed|texted)|one[- ]time|security code|enter the code|(2|two)[- ]step|authenticat/i;

/**
 * Is the page asking to confirm the sign-in? null, or how: sms (a code by text, as Sainsbury's always does),
 * email_code (a code sent by email, typed here), email_link (a link in an email to click, then Continue here).
 */
async function challenge(page) {
  const url = page.url();
  if (url.includes('/mfa')) return 'sms';
  // The sign-in form itself (with an error on it, say) is not a confirmation step.
  const form = await page.locator(PASSWORD).first().isVisible().catch(() => false);
  if (form && (await page.locator(EMAIL).first().isVisible().catch(() => false))) return null;
  const text = await page.locator('body').first().innerText({ timeout: 3000 }).catch(() => '');
  if (!CHECK_WORDS.test(text) && !/verif|challenge|otp|2fa|confirm/i.test(url)) return null;
  const box = await page.locator(CODE_BOX).first().isVisible().catch(() => false);
  if (box) return /\b(text|sms|phone|mobile)\b/i.test(text) && !/\bemail\b/i.test(text) ? 'sms' : 'email_code';
  return 'email_link';
}

/** Where to look to see whether the sign-in went through. */
const HOME = { ocado: 'https://www.ocado.com/', sainsburys: 'https://www.sainsburys.co.uk/gol-ui/groceries' };

/**
 * One login at a time. Each retailer has its own browser profile, kept in ~/browser/<retailer>
 * (WA_DATA_DIR/supermarket/browser), so a sign-in it has confirmed once (an email link, a "new device" check) is a
 * known device next time. `open(provider)` gives { context, close }: playwright's persistent context, or a fake in
 * the tests. A login that needs confirming holds the page open until HOLD_MS passes.
 */
class Logins {
  constructor({ open, launch, now = () => new Date(), holdMs = HOLD_MS } = {}) {
    this.open = open || (launch
      ? async () => { const b = await launch({ headless: true }); const c = await b.newContext({ userAgent: UA }); return { context: c, close: () => b.close() }; }
      : async (provider) => {
        const { chromium } = await import('playwright');
        const dir = path.join(os.homedir(), 'browser', provider);
        fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
        const context = await chromium.launchPersistentContext(dir, {
          headless: true, args: ['--disable-blink-features=AutomationControlled'],
          userAgent: UA, locale: 'en-GB', timezoneId: 'Europe/London', viewport: { width: 1280, height: 900 },
        });
        return { context, close: () => context.close() };
      });
    this.now = now;
    this.holdMs = holdMs;
    this.pending = null; // { provider, kind, context, close, page, until, timer, screenshot }
  }

  state() {
    const p = this.pending;
    if (!p) return null;
    const out = { provider: p.provider, kind: p.kind, until: new Date(p.until).toISOString(), screenshot: p.screenshot || null };
    if (p.kind !== 'email_link') out.waiting_for_code = p.provider; // the name the page used before
    return out;
  }

  async close() {
    const p = this.pending;
    this.pending = null;
    if (p) {
      clearTimeout(p.timer);
      await p.close().catch(() => {});
    }
  }

  async hold(provider, kind, session) {
    const until = this.now().getTime() + this.holdMs;
    const timer = setTimeout(() => { this.close(); }, this.holdMs);
    timer.unref?.();
    const screenshot = await keepScreenshot(session.page, provider, this.now());
    this.pending = { provider, kind, ...session, until, timer, screenshot };
    const how = {
      sms: `${LABELS[provider]} has sent a code to your phone: type it in`,
      email_code: `${LABELS[provider]} has emailed a code: type it in`,
      email_link: `${LABELS[provider]} has emailed a link to confirm it's you: click it, then press Continue`,
    }[kind];
    return { status: 'verify_needed', kind, provider, until: new Date(until).toISOString(), message: `${how} within ${Math.round(this.holdMs / 60000)} minutes.`, screenshot };
  }

  async done(provider, context) {
    const saved = writeSession(provider, await context.cookies(), this.now());
    return { status: 'logged_in', session: summary(provider, saved, this.now()) };
  }

  async start(provider, email, password) {
    if (!email || !password) {
      throw Object.assign(new Error(`No ${LABELS[provider] || provider} email and password saved.`), { statusCode: 400 });
    }
    await this.close();
    const { context, close } = await this.open(provider);
    let page = null;
    try {
      page = await context.newPage();
      const url = provider === 'ocado' ? 'https://www.ocado.com/login' : 'https://www.sainsburys.co.uk/gol-ui/oauth/login';
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(3000);
      await dropCookieBanner(page);
      // The remembered profile may still be signed in: then there is no form, and the login is already done.
      if (!page.url().includes('login') && !(await page.locator(EMAIL).first().isVisible().catch(() => false))) {
        const out = await this.done(provider, context);
        await close();
        return out;
      }
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
      const kind = await challenge(page);
      if (kind) return await this.hold(provider, kind, { context, close, page });
      if (page.url().includes('/login')) throw Object.assign(new Error('Still on the login page: check the email and password.'), { statusCode: 401 });
      const out = await this.done(provider, context);
      await close();
      return out;
    } catch (e) {
      if (this.pending) throw e;
      const shot = page ? await keepScreenshot(page, provider, this.now()) : null;
      await close().catch(() => {});
      throw withShot(e, shot);
    }
  }

  /** The code from the text or email (Sainsbury's SMS, or a code Ocado emails). */
  async code(code, provider) {
    const p = this.pending;
    if (!p || (provider && p.provider !== provider)) {
      throw Object.assign(new Error('No login is waiting for a code. Start the login again.'), { statusCode: 409 });
    }
    if (p.kind === 'email_link') throw Object.assign(new Error('This one is a link in the email: click it, then press Continue.'), { statusCode: 400 });
    const c = String(code || '').trim();
    if (!/^[A-Za-z0-9]{4,10}$/.test(c)) throw Object.assign(new Error('That does not look like the code (4 to 10 letters or digits).'), { statusCode: 400 });
    try {
      await p.page.locator(CODE_BOX).first().fill(c);
      await dropCookieBanner(p.page);
      await submit(p.page, ['button[data-testid="submit-code"]', 'button[type="submit"]'], CODE_BOX);
      await p.page.waitForTimeout(6000);
      const at = p.page.url();
      if (at.includes('login') || (await challenge(p.page))) {
        throw Object.assign(new Error(`${LABELS[p.provider]} did not accept that code. Start the login again for a new one.`), { statusCode: 401 });
      }
      return await this.done(p.provider, p.context);
    } catch (e) {
      throw withShot(e, await keepScreenshot(p.page, p.provider, this.now()));
    } finally {
      await this.close();
    }
  }

  /** After clicking the link in the email: look again. Still waiting keeps the browser open for another try. */
  async continue(provider) {
    const p = this.pending;
    if (!p || (provider && p.provider !== provider)) {
      throw Object.assign(new Error('No login is waiting. Start the login again.'), { statusCode: 409 });
    }
    await p.page.goto(HOME[p.provider], { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
    await p.page.waitForTimeout(4000);
    const signedOut = p.page.url().includes('login') || (await challenge(p.page))
      || (await p.page.locator(EMAIL).first().isVisible().catch(() => false));
    if (signedOut) {
      p.screenshot = await keepScreenshot(p.page, p.provider, this.now());
      throw Object.assign(new Error(`${LABELS[p.provider]} has not confirmed it yet. Click the link in the newest email, then press Continue again (or start the login again).`), { statusCode: 409, screenshot: p.screenshot });
    }
    try {
      return await this.done(p.provider, p.context);
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

module.exports = { Logins, writeSession, readSession, normaliseCookies, summary, sessionFile, latestScreenshot, challenge };
