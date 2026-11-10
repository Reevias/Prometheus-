// Small helper for calling the JSON API
async function api(path, body) {
  const options = body === undefined
    ? { credentials: 'same-origin' }
    : {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      };
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

// Who is logged in? Resolves to the user object, or null for guests.
const currentUser = api('/api/me')
  .then(({ ok, data }) => (ok ? data.user : null))
  .catch(() => null);

// Show elements marked data-when="user" or data-when="guest" to match login state
currentUser.then((user) => {
  document.querySelectorAll('[data-when]').forEach((el) => {
    el.hidden = el.dataset.when !== (user ? 'user' : 'guest');
  });
});

// Mobile navigation toggle
const toggle = document.querySelector('.nav-toggle');
const nav = document.getElementById('site-nav');

if (toggle && nav) {
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(open));
  });

  // Close the menu after choosing a link
  nav.addEventListener('click', (e) => {
    if (e.target.closest('a')) {
      nav.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
}

// Footer year
const year = document.getElementById('year');
if (year) year.textContent = new Date().getFullYear();

// Contact form placeholder handler — replace with a real submission endpoint
const form = document.getElementById('contact-form');
if (form) {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const status = form.querySelector('.form-status');
    status.textContent = 'Thanks! Your message has been noted.';
    form.reset();
  });
}
