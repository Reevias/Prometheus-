const test = require('node:test');
const assert = require('node:assert');
const { openDatabase } = require('../src/db');
const { createApp } = require('../src/app');

let server;
let base;
const sent = [];
const fakeMailer = { send: async (email) => { sent.push(email); } };

test.before(async () => {
  const app = createApp(openDatabase(':memory:'), { mailer: fakeMailer, appUrl: 'https://example.test' });
  server = app.listen(0);
  await new Promise((resolve) => server.once('listening', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => server.close());

async function post(path, body, cookie) {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(cookie && { Cookie: cookie }) },
    body: JSON.stringify(body),
  });
  return { res, data: await res.json() };
}

async function me(cookie) {
  const res = await fetch(`${base}/api/me`, { headers: cookie ? { Cookie: cookie } : {} });
  return { res, data: await res.json() };
}

const sessionCookie = (res) => res.headers.get('set-cookie').split(';')[0];

test('sign up creates an account and logs the user in', async () => {
  const { res, data } = await post('/api/signup', { name: 'Ada', email: 'Ada@Example.com', password: 'correct horse' });
  assert.strictEqual(res.status, 201);
  assert.strictEqual(data.user.email, 'ada@example.com');
  assert.ok(!('password_hash' in data.user));

  const setCookie = res.headers.get('set-cookie');
  assert.match(setCookie, /HttpOnly/i);
  assert.match(setCookie, /SameSite=Lax/i);

  const session = await me(sessionCookie(res));
  assert.strictEqual(session.res.status, 200);
  assert.strictEqual(session.data.user.name, 'Ada');
});

test('sign up rejects a duplicate email regardless of case', async () => {
  const { res, data } = await post('/api/signup', { name: 'Ada 2', email: 'ADA@example.com', password: 'another password' });
  assert.strictEqual(res.status, 409);
  assert.ok(data.fields.email);
});

test('sign up validates fields', async () => {
  const { res, data } = await post('/api/signup', { name: '', email: 'not-an-email', password: 'short' });
  assert.strictEqual(res.status, 400);
  assert.deepStrictEqual(Object.keys(data.fields).sort(), ['email', 'name', 'password']);
});

test('log in works with the right password and fails with the wrong one', async () => {
  const bad = await post('/api/login', { email: 'ada@example.com', password: 'wrong password' });
  assert.strictEqual(bad.res.status, 401);

  const unknown = await post('/api/login', { email: 'nobody@example.com', password: 'whatever123' });
  assert.strictEqual(unknown.res.status, 401);
  assert.strictEqual(unknown.data.error, bad.data.error);

  const good = await post('/api/login', { email: ' ada@example.com ', password: 'correct horse' });
  assert.strictEqual(good.res.status, 200);
  assert.strictEqual((await me(sessionCookie(good.res))).res.status, 200);
});

test('log out ends the session', async () => {
  const { res } = await post('/api/login', { email: 'ada@example.com', password: 'correct horse' });
  const cookie = sessionCookie(res);
  await post('/api/logout', {}, cookie);
  assert.strictEqual((await me(cookie)).res.status, 401);
});

test('repeated failed logins are rate limited', async () => {
  await post('/api/signup', { name: 'Bob', email: 'bob@example.com', password: 'bobs password' });
  for (let i = 0; i < 10; i++) await post('/api/login', { email: 'bob@example.com', password: 'nope nope' });
  const blocked = await post('/api/login', { email: 'bob@example.com', password: 'bobs password' });
  assert.strictEqual(blocked.res.status, 429);
});

test('non-JSON POSTs are rejected (CSRF protection)', async () => {
  const res = await fetch(`${base}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'email=ada%40example.com&password=correct+horse',
  });
  assert.strictEqual(res.status, 415);
});

test('guests are not logged in and pages are served', async () => {
  assert.strictEqual((await me()).res.status, 401);
  for (const page of ['/', '/login', '/signup.html', '/account.html']) {
    assert.strictEqual((await fetch(base + page)).status, 200, page);
  }
});

// Pulls the one-time token out of the most recent email sent to an address.
async function tokenFromEmail(to, page) {
  await new Promise((r) => setImmediate(r)); // emails are sent in the background
  const email = sent.filter((m) => m.to === to).at(-1);
  assert.ok(email, `no email sent to ${to}`);
  const match = email.text.match(new RegExp(`https://example\\.test/${page}\\?token=(\\S+)`));
  assert.ok(match, `no ${page} link in email`);
  return decodeURIComponent(match[1]);
}

test('sign up sends a confirmation email that verifies the address once', async () => {
  const { res } = await post('/api/signup', { name: 'Cat', email: 'cat@example.com', password: 'cat password' });
  const cookie = sessionCookie(res);
  assert.strictEqual((await me(cookie)).data.user.emailVerified, false);

  const token = await tokenFromEmail('cat@example.com', 'verify-email.html');
  assert.match(sent.at(-1).html, /Confirm my email/);

  assert.strictEqual((await post('/api/verify-email', { token })).res.status, 200);
  assert.strictEqual((await me(cookie)).data.user.emailVerified, true);
  assert.strictEqual((await post('/api/verify-email', { token })).res.status, 400, 'token is single-use');
});

test('resend verification requires login and is rate limited', async () => {
  assert.strictEqual((await post('/api/resend-verification', {})).res.status, 401);

  const { res } = await post('/api/signup', { name: 'Dan', email: 'dan@example.com', password: 'dan password' });
  const cookie = sessionCookie(res);
  // The sign-up email counts toward the one-per-minute limit.
  assert.strictEqual((await post('/api/resend-verification', {}, cookie)).res.status, 429);
});

test('forgot password gives the same answer for unknown emails', async () => {
  const before = sent.length;
  const { res } = await post('/api/forgot-password', { email: 'ghost@example.com' });
  assert.strictEqual(res.status, 200);
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(sent.length, before);
});

test('reset links work, are single-use and reject weak passwords', async () => {
  // Use a separate app so the email cooldown starts clean.
  const db = openDatabase(':memory:');
  const mails = [];
  const app = createApp(db, { mailer: { send: async (m) => { mails.push(m); } }, appUrl: 'https://example.test' });
  const srv = app.listen(0);
  await new Promise((r) => srv.once('listening', r));
  const url = `http://127.0.0.1:${srv.address().port}`;
  const call = async (path, body, cookie) => {
    const res = await fetch(url + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(cookie && { Cookie: cookie }) },
      body: JSON.stringify(body),
    });
    return { res, data: await res.json() };
  };

  try {
    const { lastInsertRowid } = db.prepare('INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)')
      .run('gus@example.com', 'Gus', require('../src/auth').hashPassword('old password'));
    const oldSession = sessionCookie((await call('/api/login', { email: 'gus@example.com', password: 'old password' })).res);

    await call('/api/forgot-password', { email: 'GUS@example.com' });
    await new Promise((r) => setImmediate(r));
    assert.strictEqual(mails.length, 1);
    const token = decodeURIComponent(mails[0].text.match(/reset-password\.html\?token=(\S+)/)[1]);

    const weak = await call('/api/reset-password', { token, password: 'short' });
    assert.strictEqual(weak.res.status, 400);

    const ok = await call('/api/reset-password', { token, password: 'brand new password' });
    assert.strictEqual(ok.res.status, 200);
    assert.strictEqual((await call('/api/reset-password', { token, password: 'another one!' })).res.status, 400);

    const oldMe = await fetch(`${url}/api/me`, { headers: { Cookie: oldSession } });
    assert.strictEqual(oldMe.status, 401, 'old sessions are logged out');

    const newMe = await fetch(`${url}/api/me`, { headers: { Cookie: sessionCookie(ok.res) } });
    const { user } = await newMe.json();
    assert.strictEqual(user.id, Number(lastInsertRowid));
    assert.strictEqual(user.emailVerified, true, 'resetting proves inbox ownership');

    assert.strictEqual((await call('/api/login', { email: 'gus@example.com', password: 'old password' })).res.status, 401);
    assert.strictEqual((await call('/api/login', { email: 'gus@example.com', password: 'brand new password' })).res.status, 200);
  } finally {
    srv.close();
  }
});

test('a recent sign-up email does not block a password reset email', async () => {
  await post('/api/signup', { name: 'Hal', email: 'hal@example.com', password: 'hal password' });
  await post('/api/forgot-password', { email: 'hal@example.com' });
  await tokenFromEmail('hal@example.com', 'reset-password.html');
});
