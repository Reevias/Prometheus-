// Sends email through Resend (https://resend.com). Without an API key, emails are
// printed to the console instead so sign-up and password reset still work locally.

const RESEND_URL = 'https://api.resend.com/emails';

function createMailer({ apiKey, from, log = console.log } = {}) {
  if (!apiKey) {
    return {
      async send({ to, subject, text }) {
        log(`\n--- Email (not sent: RESEND_API_KEY is not set) ---\nTo: ${to}\nSubject: ${subject}\n\n${text}\n---\n`);
      },
    };
  }

  return {
    async send({ to, subject, html, text }) {
      const res = await fetch(RESEND_URL, {
        method: 'POST',
        headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to: [to], subject, html, text }),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        throw new Error(`Resend responded ${res.status}: ${detail}`);
      }
    },
  };
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

// A simple, widely compatible email layout with one call-to-action button.
function buttonEmail({ siteName, heading, intro, buttonLabel, url, footer }) {
  const html = `<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f6f8;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:#1d2330">
  <table role="presentation" width="100%" style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #dfe3ea;border-radius:14px">
    <tr><td style="padding:32px">
      <p style="margin:0 0 24px;font-weight:700;font-size:18px">${escapeHtml(siteName)}</p>
      <h1 style="margin:0 0 12px;font-size:22px">${escapeHtml(heading)}</h1>
      <p style="margin:0 0 24px;line-height:1.6;color:#5b6475">${escapeHtml(intro)}</p>
      <a href="${escapeHtml(url)}" style="display:inline-block;padding:12px 24px;background:#2f5bea;color:#ffffff;text-decoration:none;font-weight:600;border-radius:10px">${escapeHtml(buttonLabel)}</a>
      <p style="margin:24px 0 0;font-size:13px;line-height:1.6;color:#5b6475">Or paste this link into your browser:<br><a href="${escapeHtml(url)}" style="color:#2f5bea;word-break:break-all">${escapeHtml(url)}</a></p>
      <p style="margin:24px 0 0;font-size:13px;color:#5b6475">${escapeHtml(footer)}</p>
    </td></tr>
  </table>
</body></html>`;
  const text = `${heading}\n\n${intro}\n\n${buttonLabel}: ${url}\n\n${footer}`;
  return { html, text };
}

module.exports = { createMailer, buttonEmail };
