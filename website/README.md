# Website Base

A responsive starter website with email sign-up and login, built with plain
HTML/CSS/JS on the front end and a small Node.js (Express + SQLite) server.

## Quick start

Requires Node.js 22 or newer.

```sh
npm install
npm start          # http://localhost:3000
npm run dev        # same, restarts on file changes
npm test           # API tests
```

## Accounts

- **Sign up** (`/signup.html`): name, email and password (8+ characters), with
  inline validation, a show/hide password toggle and a strength meter.
- **Log in** (`/login.html`): email and password.
- **Confirm email**: after sign-up, an email with a confirmation link is sent. The
  account page shows a reminder and a "Resend email" button until it's confirmed.
- **Forgot password** (`/forgot-password.html`): emails a link (valid 1 hour) to
  choose a new password. Resetting logs out every other session.
- **My account** (`/account.html`): shows the user's details, confirmation status
  and a log-out button. Guests are sent to the login page.
- The header and home page switch between "Log in / Sign up" and "My account"
  automatically.

### How it works

| Endpoint            | Purpose                                  |
| ------------------- | ---------------------------------------- |
| `POST /api/signup`  | Create an account and start a session    |
| `POST /api/login`   | Log in and start a session               |
| `POST /api/logout`  | End the current session                  |
| `GET  /api/me`      | Return the logged-in user (401 if none)  |
| `POST /api/verify-email` | Confirm an email address from the link in the email |
| `POST /api/resend-verification` | Send the confirmation email again |
| `POST /api/forgot-password` | Email a password reset link     |
| `POST /api/reset-password`  | Set a new password using that link |

Security measures:

- Passwords are hashed with scrypt and a random per-user salt; they are never stored in plain text.
- Sessions use a random token in an `HttpOnly`, `SameSite=Lax` cookie (also `Secure`
  when `NODE_ENV=production`). Only a SHA-256 hash of the token is stored. Sessions last 7 days.
- Emails are case-insensitive and unique.
- Failed logins are limited to 10 per 15 minutes per IP + email.
- API writes must be JSON, which together with `SameSite` cookies blocks cross-site form attacks.
- Email links are single-use and expire (24 hours to confirm, 1 hour to reset); only hashes are stored.
- "Forgot password" answers the same way whether or not an account exists, so it can't reveal who has signed up.
- At most one email of each kind per address per minute.

## Sending email (Resend)

Emails are sent with [Resend](https://resend.com) using its HTTP API (no extra package needed).

1. Create a free account at resend.com (3,000 emails/month on the free plan).
2. Add and verify your domain under **Domains** (Resend shows the DNS records to add).
3. Create an API key under **API Keys**.
4. Set `RESEND_API_KEY`, `EMAIL_FROM` and `APP_URL` on your server (see `.env.example`).

Without `RESEND_API_KEY`, emails are printed in the server console instead, so you can
click the links while developing. Resend's test sender `onboarding@resend.dev` (the
default) only delivers to the email address on your Resend account.

## Structure

```
.
├── server.js           # Entry point
├── src/
│   ├── app.js          # Express app and API routes
│   ├── auth.js         # Password hashing, tokens, validation
│   ├── db.js           # SQLite setup (users, sessions, email tokens)
│   └── mailer.js       # Resend email sending and email template
├── public/             # Static site
│   ├── index.html
│   ├── login.html / signup.html / account.html
│   ├── forgot-password.html / reset-password.html / verify-email.html
│   ├── css/styles.css  # Design tokens at the top, dark mode included
│   └── js/main.js, js/auth.js
└── test/auth.test.js
```

## Configuration

| Variable   | Default        | Notes                                    |
| ---------- | -------------- | ---------------------------------------- |
| `PORT`     | `3000`         |                                          |
| `DB_FILE`  | `data/app.db`  | SQLite database file (git-ignored)       |
| `NODE_ENV` | —              | Set to `production` to mark cookies `Secure` (requires HTTPS) |
| `APP_URL`  | `http://localhost:PORT` | Public site address used in email links |
| `RESEND_API_KEY` | —        | Resend API key; unset = print emails to console |
| `EMAIL_FROM` | `My Website <onboarding@resend.dev>` | Sender address (verified domain in Resend) |

## Not included yet

- The contact form on the home page only shows a confirmation; connect it to a backend before going live.

> This folder is the original sign-up/login website, moved here from the old repository (Reevias/Claude-testing-). It is separate from Prometheus: run it with `npm install` and `npm start` inside this folder.
