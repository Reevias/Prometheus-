// Login, sign-up and account page behaviour. Relies on api() and currentUser from main.js.

const authForm = document.querySelector('[data-auth-form]');
const accountCard = document.querySelector('[data-account]');

// ---------- Show / hide password ----------
document.querySelectorAll('.toggle-password').forEach((btn) => {
  btn.addEventListener('click', () => {
    const input = document.getElementById(btn.getAttribute('aria-controls'));
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.textContent = show ? 'Hide' : 'Show';
    btn.setAttribute('aria-pressed', String(show));
  });
});

// ---------- Password strength meter (sign-up) ----------
const strength = document.querySelector('.strength');
if (strength) {
  const bar = strength.querySelector('span');
  document.getElementById('password').addEventListener('input', (e) => {
    const pw = e.target.value;
    let score = 0;
    if (pw.length >= 8) score++;
    if (pw.length >= 12) score++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
    if (/\d/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw)) score++;
    bar.style.width = pw ? `${Math.max(1, score) * 20}%` : '0';
    strength.dataset.level = score <= 2 ? 'weak' : score <= 3 ? 'ok' : 'strong';
  });
}

// ---------- Form helpers ----------
function setFieldError(form, name, message) {
  const input = form.elements[name];
  const error = form.querySelector(`#${name}-error`);
  if (!input || !error) return;
  error.textContent = message || '';
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
}

function clearErrors(form) {
  form.querySelectorAll('.field-error').forEach((el) => { el.textContent = ''; });
  form.querySelectorAll('[aria-invalid]').forEach((el) => el.removeAttribute('aria-invalid'));
  const alert = form.querySelector('.form-alert');
  alert.hidden = true;
  alert.textContent = '';
}

function showAlert(form, message) {
  const alert = form.querySelector('.form-alert');
  alert.textContent = message;
  alert.hidden = false;
}

// Quick checks in the browser so people get instant feedback; the server re-checks everything.
function validate(form, mode) {
  const errors = {};
  const { name, email, password } = form.elements;
  if (name && !name.value.trim()) errors.name = 'Please enter your name.';
  if (email) {
    if (!email.value.trim()) errors.email = 'Please enter your email.';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) errors.email = 'That email doesn\'t look right.';
  }
  if (password) {
    if (!password.value) errors.password = 'Please enter your password.';
    else if (mode !== 'login' && password.value.length < 8) errors.password = 'Password must be at least 8 characters.';
  }
  return errors;
}

// What each form sends, where, and what happens on success
const resetToken = new URLSearchParams(location.search).get('token');
const FORMS = {
  login: {
    busy: 'Logging in…',
    endpoint: '/api/login',
    body: (f) => ({ email: f.elements.email.value, password: f.elements.password.value }),
    done: () => location.assign('account.html'),
  },
  signup: {
    busy: 'Creating account…',
    endpoint: '/api/signup',
    body: (f) => ({ name: f.elements.name.value, email: f.elements.email.value, password: f.elements.password.value }),
    done: () => location.assign('account.html'),
  },
  forgot: {
    busy: 'Sending…',
    endpoint: '/api/forgot-password',
    body: (f) => ({ email: f.elements.email.value }),
    done: (f) => {
      document.querySelector('[data-sent-to]').textContent = f.elements.email.value.trim();
      f.hidden = true;
      document.querySelector('[data-done]').hidden = false;
    },
  },
  reset: {
    busy: 'Saving…',
    endpoint: '/api/reset-password',
    body: (f) => ({ token: resetToken, password: f.elements.password.value }),
    done: () => location.assign('account.html?reset=1'),
  },
};

// ---------- Login / sign-up forms ----------
if (authForm) {
  const mode = authForm.dataset.authForm;

  const config = FORMS[mode];

  // Already logged in? Skip straight to the account page.
  if (mode === 'login' || mode === 'signup') {
    currentUser.then((user) => { if (user) location.replace('account.html'); });
  }

  if (mode === 'reset' && !resetToken) {
    showAlert(authForm, 'This page needs the link from your reset email. Request a new one below.');
    authForm.querySelector('button[type="submit"]').disabled = true;
  }

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearErrors(authForm);

    const errors = validate(authForm, mode);
    if (Object.keys(errors).length) {
      Object.entries(errors).forEach(([name, msg]) => setFieldError(authForm, name, msg));
      authForm.querySelector('[aria-invalid="true"]').focus();
      return;
    }

    const button = authForm.querySelector('button[type="submit"]');
    const label = button.textContent;
    button.disabled = true;
    button.textContent = config.busy;

    try {
      const { ok, data } = await api(config.endpoint, config.body(authForm));
      if (ok) {
        config.done(authForm);
        return;
      }
      if (data.fields) Object.entries(data.fields).forEach(([name, msg]) => setFieldError(authForm, name, msg));
      showAlert(authForm, data.error || 'Something went wrong. Please try again.');
    } catch {
      showAlert(authForm, 'Couldn\'t reach the server. Check your connection and try again.');
    }
    button.disabled = false;
    button.textContent = label;
  });
}

// ---------- Account page ----------
if (accountCard) {
  currentUser.then((user) => {
    if (!user) {
      location.replace('login.html');
      return;
    }
    const values = {
      name: user.name,
      email: user.email,
      status: user.emailVerified ? 'Confirmed' : 'Not confirmed',
      initial: user.name.trim().charAt(0).toUpperCase(),
      createdAt: new Date(`${user.createdAt.replace(' ', 'T')}Z`).toLocaleDateString(undefined, { dateStyle: 'long' }),
    };
    accountCard.querySelectorAll('[data-field]').forEach((el) => {
      el.textContent = values[el.dataset.field];
    });
    accountCard.querySelector('.badge').dataset.verified = String(user.emailVerified);
    accountCard.querySelector('[data-unverified]').hidden = user.emailVerified;
    if (new URLSearchParams(location.search).has('reset')) {
      accountCard.querySelector('.auth-sub').textContent = 'Your password has been changed and you\'re logged in.';
    }
    document.querySelector('[data-loading]').hidden = true;
    accountCard.hidden = false;
  });

  accountCard.querySelector('[data-logout]').addEventListener('click', async () => {
    await api('/api/logout', {}).catch(() => {});
    location.assign('./');
  });
}

// Resend the confirmation email from the account page
const resendButton = document.querySelector('[data-resend]');
if (resendButton) {
  resendButton.addEventListener('click', async () => {
    const status = document.querySelector('.notice-status');
    resendButton.disabled = true;
    status.textContent = 'Sending…';
    try {
      const { ok, data } = await api('/api/resend-verification', {});
      status.textContent = ok ? 'Sent! Check your inbox (and spam folder).' : data.error;
    } catch {
      status.textContent = 'Couldn\'t reach the server. Please try again.';
    }
    setTimeout(() => { resendButton.disabled = false; }, 5000);
  });
}

// ---------- Email confirmation page ----------
const verifyCard = document.querySelector('[data-verify]');
if (verifyCard) {
  const show = (icon, title, message, ok) => {
    verifyCard.querySelector('[data-verify-icon]').textContent = icon;
    verifyCard.querySelector('[data-verify-icon]').dataset.state = ok ? 'ok' : 'error';
    verifyCard.querySelector('[data-verify-title]').textContent = title;
    verifyCard.querySelector('[data-verify-message]').textContent = message;
    verifyCard.querySelector('[data-verify-action]').hidden = false;
  };
  const token = new URLSearchParams(location.search).get('token');
  api('/api/verify-email', { token: token || '' })
    .then(({ ok, data }) => {
      if (ok) show('\u2713', 'Email confirmed', 'Thanks! Your email address is confirmed.', true);
      else show('!', 'Link not valid', `${data.error} You can send a new one from your account page.`, false);
    })
    .catch(() => show('!', 'Something went wrong', 'Couldn\'t reach the server. Please try again.', false));
}
