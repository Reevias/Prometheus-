const path = require('node:path');
const { openDatabase } = require('./src/db');
const { createApp } = require('./src/app');
const { createMailer } = require('./src/mailer');

const PORT = Number(process.env.PORT) || 3000;
const DB_FILE = process.env.DB_FILE || path.join(__dirname, 'data', 'app.db');
const isProduction = process.env.NODE_ENV === 'production';
const APP_URL = (process.env.APP_URL || `http://localhost:${PORT}`).replace(/\/$/, '');

const db = openDatabase(DB_FILE);
const mailer = createMailer({
  apiKey: process.env.RESEND_API_KEY,
  from: process.env.EMAIL_FROM || 'My Website <onboarding@resend.dev>',
});
if (!process.env.RESEND_API_KEY) {
  console.log('RESEND_API_KEY is not set: emails will be printed here instead of sent.');
}

const app = createApp(db, { secureCookies: isProduction, mailer, appUrl: APP_URL });

app.listen(PORT, () => {
  console.log(`Server running at http://localhost:${PORT}`);
});
