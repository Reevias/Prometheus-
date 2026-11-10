const crypto = require('node:crypto');

const SCRYPT_PARAMS = { N: 16384, r: 8, p: 1 };
const KEY_LENGTH = 64;

function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const key = crypto.scryptSync(password, salt, KEY_LENGTH, SCRYPT_PARAMS);
  return `scrypt$${salt.toString('base64')}$${key.toString('base64')}`;
}

function verifyPassword(password, stored) {
  const [scheme, saltB64, keyB64] = String(stored).split('$');
  if (scheme !== 'scrypt' || !saltB64 || !keyB64) return false;

  const expected = Buffer.from(keyB64, 'base64');
  const actual = crypto.scryptSync(password, Buffer.from(saltB64, 'base64'), expected.length, SCRYPT_PARAMS);
  return crypto.timingSafeEqual(actual, expected);
}

// A hash of a random password, used to keep login timing the same whether or not the email exists.
const DUMMY_HASH = hashPassword(crypto.randomBytes(16).toString('hex'));

function newSessionToken() {
  return crypto.randomBytes(32).toString('base64url');
}

function hashToken(token) {
  return crypto.createHash('sha256').update(token).digest('hex');
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validatePassword(password) {
  if (typeof password !== 'string' || password.length < 8) return 'Password must be at least 8 characters.';
  if (password.length > 200) return 'Password must be 200 characters or fewer.';
  return null;
}

function validateSignup({ name, email, password }) {
  const errors = {};
  if (typeof name !== 'string' || !name.trim()) errors.name = 'Please enter your name.';
  else if (name.trim().length > 100) errors.name = 'Name must be 100 characters or fewer.';

  if (typeof email !== 'string' || !EMAIL_RE.test(email.trim()) || email.length > 254) {
    errors.email = 'Please enter a valid email address.';
  }

  const passwordError = validatePassword(password);
  if (passwordError) errors.password = passwordError;
  return errors;
}

module.exports = { hashPassword, verifyPassword, DUMMY_HASH, newSessionToken, hashToken, validatePassword, validateSignup };
