const path = require('node:path');
const express = require('express');
const {
  hashPassword,
  verifyPassword,
  DUMMY_HASH,
  newSessionToken,
  hashToken,
  validatePassword,
  validateSignup,
} = require('./auth');
const { createMailer, buttonEmail } = require('./mailer');

const SESSION_COOKIE = 'sid';
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const LOGIN_MAX_ATTEMPTS = 10;
const VERIFY_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
const RESET_TTL_MS = 60 * 60 * 1000; // 1 hour
const EMAIL_COOLDOWN_MS = 60 * 1000; // at most one email per address per minute

function parseCookies(header = '') {
  const cookies = {};
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const key = part.slice(0, i).trim();
    if (key) cookies[key] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return cookies;
}

function createApp(db, {
  secureCookies = false,
  mailer = createMailer(),
  appUrl = 'http://localhost:3000',
  siteName = 'My Website',
} = {}) {
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', 'loopback');

  const stmts = {
    insertUser: db.prepare('INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)'),
    userByEmail: db.prepare('SELECT * FROM users WHERE email = ?'),
    insertSession: db.prepare('INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)'),
    sessionUser: db.prepare(`
      SELECT u.id, u.email, u.name, u.email_verified_at, u.created_at
      FROM sessions s JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = ? AND s.expires_at > ?`),
    deleteSession: db.prepare('DELETE FROM sessions WHERE token_hash = ?'),
    deleteExpired: db.prepare('DELETE FROM sessions WHERE expires_at <= ?'),
    deleteUserSessions: db.prepare('DELETE FROM sessions WHERE user_id = ?'),
    insertEmailToken: db.prepare('INSERT INTO email_tokens (token_hash, user_id, purpose, expires_at) VALUES (?, ?, ?, ?)'),
    emailToken: db.prepare('SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = ? AND expires_at > ?'),
    deleteEmailTokens: db.prepare('DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?'),
    markVerified: db.prepare("UPDATE users SET email_verified_at = datetime('now') WHERE id = ? AND email_verified_at IS NULL"),
    setPassword: db.prepare('UPDATE users SET password_hash = ? WHERE id = ?'),
  };

  // Simple in-memory limiter for failed logins, keyed by IP + email.
  const failedLogins = new Map();
  function tooManyAttempts(key) {
    const entry = failedLogins.get(key);
    if (!entry || Date.now() - entry.first > LOGIN_WINDOW_MS) return false;
    return entry.count >= LOGIN_MAX_ATTEMPTS;
  }
  function recordFailure(key) {
    const entry = failedLogins.get(key);
    if (!entry || Date.now() - entry.first > LOGIN_WINDOW_MS) failedLogins.set(key, { first: Date.now(), count: 1 });
    else entry.count += 1;
  }

  function startSession(res, userId) {
    const token = newSessionToken();
    stmts.insertSession.run(hashToken(token), userId, Date.now() + SESSION_TTL_MS);
    res.cookie(SESSION_COOKIE, token, {
      httpOnly: true,
      sameSite: 'lax',
      secure: secureCookies,
      maxAge: SESSION_TTL_MS,
      path: '/',
    });
  }

  function publicUser(u) {
    return {
      id: u.id,
      email: u.email,
      name: u.name,
      emailVerified: Boolean(u.email_verified_at),
      createdAt: u.created_at,
    };
  }

  // Creates a one-time link, replacing any earlier link of the same kind.
  function createEmailLink(userId, purpose, ttl, page) {
    const token = newSessionToken();
    stmts.deleteEmailTokens.run(userId, purpose);
    stmts.insertEmailToken.run(hashToken(token), userId, purpose, Date.now() + ttl);
    return `${appUrl}/${page}?token=${encodeURIComponent(token)}`;
  }

  // Looks up a one-time link token; returns the user id, or null if it is invalid or expired.
  function useEmailToken(token, purpose) {
    if (typeof token !== 'string' || !token) return null;
    const row = stmts.emailToken.get(hashToken(token), purpose, Date.now());
    if (!row) return null;
    stmts.deleteEmailTokens.run(row.user_id, purpose);
    return row.user_id;
  }

  // Tracked per address and email type, so a sign-up email doesn't block a password reset.
  const lastEmailSent = new Map();
  function emailOnCooldown(purpose, address) {
    const key = `${purpose}|${address}`;
    const last = lastEmailSent.get(key);
    if (last && Date.now() - last < EMAIL_COOLDOWN_MS) return true;
    lastEmailSent.set(key, Date.now());
    return false;
  }

  function sendVerificationEmail(user) {
    const url = createEmailLink(user.id, 'verify', VERIFY_TTL_MS, 'verify-email.html');
    return mailer.send({
      to: user.email,
      subject: `Confirm your email for ${siteName}`,
      ...buttonEmail({
        siteName,
        heading: `Welcome, ${user.name}!`,
        intro: 'Please confirm your email address so we know it\'s really you.',
        buttonLabel: 'Confirm my email',
        url,
        footer: 'This link expires in 24 hours. If you didn\'t create an account, you can ignore this email.',
      }),
    });
  }

  function sendResetEmail(user) {
    const url = createEmailLink(user.id, 'reset', RESET_TTL_MS, 'reset-password.html');
    return mailer.send({
      to: user.email,
      subject: `Reset your ${siteName} password`,
      ...buttonEmail({
        siteName,
        heading: 'Reset your password',
        intro: 'We received a request to reset your password. Click the button below to choose a new one.',
        buttonLabel: 'Choose a new password',
        url,
        footer: 'This link expires in 1 hour. If you didn\'t ask to reset your password, you can ignore this email.',
      }),
    });
  }

  // Send in the background so a slow mail provider doesn't slow down the page.
  function sendLater(promise) {
    promise.catch((err) => console.error('Failed to send email:', err.message));
  }

  // Attach the logged-in user (if any) to every request.
  app.use((req, res, next) => {
    const token = parseCookies(req.headers.cookie)[SESSION_COOKIE];
    req.sessionTokenHash = token ? hashToken(token) : null;
    req.user = req.sessionTokenHash ? stmts.sessionUser.get(req.sessionTokenHash, Date.now()) : null;
    next();
  });

  // State-changing API calls must be JSON. Browsers can't send cross-site JSON
  // without a CORS preflight, which together with SameSite cookies blocks CSRF.
  app.use('/api', (req, res, next) => {
    if (req.method !== 'GET' && !req.is('application/json')) {
      return res.status(415).json({ error: 'Requests must be sent as JSON.' });
    }
    next();
  });
  app.use(express.json({ limit: '10kb' }));

  app.post('/api/signup', (req, res) => {
    const { name, email, password } = req.body || {};
    const errors = validateSignup({ name, email, password });
    if (Object.keys(errors).length) {
      return res.status(400).json({ error: 'Please fix the highlighted fields.', fields: errors });
    }

    const normalizedEmail = email.trim().toLowerCase();
    if (stmts.userByEmail.get(normalizedEmail)) {
      return res.status(409).json({
        error: 'An account with this email already exists.',
        fields: { email: 'This email is already registered. Try logging in instead.' },
      });
    }

    const { lastInsertRowid } = stmts.insertUser.run(normalizedEmail, name.trim(), hashPassword(password));
    const user = stmts.userByEmail.get(normalizedEmail);
    startSession(res, lastInsertRowid);
    emailOnCooldown('verify', normalizedEmail);
    sendLater(sendVerificationEmail(user));
    res.status(201).json({ user: publicUser(user) });
  });

  app.post('/api/login', (req, res) => {
    const { email, password } = req.body || {};
    if (typeof email !== 'string' || typeof password !== 'string' || !email || !password) {
      return res.status(400).json({ error: 'Please enter your email and password.' });
    }

    const normalizedEmail = email.trim().toLowerCase();
    const limitKey = `${req.ip}|${normalizedEmail}`;
    if (tooManyAttempts(limitKey)) {
      return res.status(429).json({ error: 'Too many failed attempts. Please wait 15 minutes and try again.' });
    }

    const user = stmts.userByEmail.get(normalizedEmail);
    const ok = verifyPassword(password, user ? user.password_hash : DUMMY_HASH);
    if (!user || !ok) {
      recordFailure(limitKey);
      return res.status(401).json({ error: 'Incorrect email or password.' });
    }

    failedLogins.delete(limitKey);
    stmts.deleteExpired.run(Date.now());
    startSession(res, user.id);
    res.json({ user: publicUser(user) });
  });

  app.post('/api/logout', (req, res) => {
    if (req.sessionTokenHash) stmts.deleteSession.run(req.sessionTokenHash);
    res.clearCookie(SESSION_COOKIE, { path: '/' });
    res.json({ ok: true });
  });

  app.post('/api/verify-email', (req, res) => {
    const userId = useEmailToken(req.body?.token, 'verify');
    if (!userId) {
      return res.status(400).json({ error: 'This confirmation link is invalid or has expired.' });
    }
    stmts.markVerified.run(userId);
    res.json({ ok: true });
  });

  app.post('/api/resend-verification', async (req, res) => {
    if (!req.user) return res.status(401).json({ error: 'Please log in first.' });
    if (req.user.email_verified_at) return res.json({ ok: true, alreadyVerified: true });
    if (emailOnCooldown('verify', req.user.email)) {
      return res.status(429).json({ error: 'We just sent you an email. Please wait a minute before asking again.' });
    }
    try {
      await sendVerificationEmail(req.user);
      res.json({ ok: true });
    } catch (err) {
      console.error('Failed to send email:', err.message);
      lastEmailSent.delete(`verify|${req.user.email}`);
      res.status(502).json({ error: 'We couldn\'t send the email right now. Please try again shortly.' });
    }
  });

  app.post('/api/forgot-password', (req, res) => {
    const email = req.body?.email;
    if (typeof email !== 'string' || !email.trim()) {
      return res.status(400).json({ error: 'Please enter your email.' });
    }
    // Always give the same answer so this can't be used to discover which emails have accounts.
    const normalizedEmail = email.trim().toLowerCase();
    const user = stmts.userByEmail.get(normalizedEmail);
    if (user && !emailOnCooldown('reset', normalizedEmail)) sendLater(sendResetEmail(user));
    res.json({ ok: true });
  });

  app.post('/api/reset-password', (req, res) => {
    const { token, password } = req.body || {};
    const passwordError = validatePassword(password);
    if (passwordError) {
      return res.status(400).json({ error: passwordError, fields: { password: passwordError } });
    }
    const userId = useEmailToken(token, 'reset');
    if (!userId) {
      return res.status(400).json({ error: 'This reset link is invalid or has expired. Please request a new one.' });
    }
    stmts.setPassword.run(hashPassword(password), userId);
    stmts.deleteUserSessions.run(userId); // log out everywhere else
    stmts.markVerified.run(userId); // they proved they own the inbox
    startSession(res, userId);
    res.json({ ok: true });
  });

  app.get('/api/me', (req, res) => {
    if (!req.user) return res.status(401).json({ error: 'Not logged in.' });
    res.json({ user: publicUser(req.user) });
  });

  app.use('/api', (req, res) => res.status(404).json({ error: 'Not found.' }));

  app.use(express.static(path.join(__dirname, '..', 'public'), { extensions: ['html'] }));

  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    if (err.type === 'entity.parse.failed') return res.status(400).json({ error: 'Invalid JSON.' });
    console.error(err);
    res.status(500).json({ error: 'Something went wrong. Please try again.' });
  });

  return app;
}

module.exports = { createApp };
