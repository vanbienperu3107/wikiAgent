/**
 * knowledge_pipeline.js
 *
 * Phase 3 (client side) of a personal knowledge system.
 *
 * Connects to WhatsApp via Baileys, listens for incoming *personal* messages,
 * buffers them per conversation, and — after a quiet period — POSTs each thread
 * to a knowledge-extraction REST endpoint for storage.
 *
 * Design notes:
 *   - Personal chats only: group chats (@g.us) and status broadcasts are skipped.
 *   - Self-authored messages (key.fromMe) are skipped.
 *   - Per-thread debounce: WA_WINDOW_MS after the LAST message in a thread, flush it.
 *   - Fully in-memory: a Map of buffers + a Map of debounce timers. No database.
 */

import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
} from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';

// ---------------------------------------------------------------------------
// Configuration (from environment) — fail fast if required values are missing.
// ---------------------------------------------------------------------------

const WIKI_API_URL = process.env.WIKI_API_URL;
const WIKI_AUTH_TOKEN = process.env.WIKI_AUTH_TOKEN;
const WA_WINDOW_MS = Number(process.env.WA_WINDOW_MS ?? 300000); // default 5 min

if (!WIKI_API_URL || !WIKI_AUTH_TOKEN) {
  console.error(
    '\n[config] Missing required environment variables.\n' +
      '  WIKI_API_URL    = ' + (WIKI_API_URL ?? '(unset)') + '\n' +
      '  WIKI_AUTH_TOKEN = ' + (WIKI_AUTH_TOKEN ? '(set)' : '(unset)') + '\n\n' +
      'Copy .env.example to .env and fill these in, e.g.:\n' +
      '  WIKI_API_URL=http://localhost:8010 WIKI_AUTH_TOKEN=... npm start\n'
  );
  process.exit(1);
}

if (!Number.isFinite(WA_WINDOW_MS) || WA_WINDOW_MS <= 0) {
  console.error(`[config] WA_WINDOW_MS must be a positive number, got: ${process.env.WA_WINDOW_MS}`);
  process.exit(1);
}

// Node 18+ ships a global fetch; this is just a defensive guard.
if (typeof fetch !== 'function') {
  console.error('[config] Global fetch is unavailable. Use Node 18+ (or install undici).');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Per-thread buffering state (in-memory only).
// ---------------------------------------------------------------------------

/** @type {Map<string, string[]>} remoteJid -> collected message texts */
const buffers = new Map();

/** @type {Map<string, NodeJS.Timeout>} remoteJid -> pending flush timer */
const timers = new Map();

/**
 * Extract plain text from a Baileys message payload.
 * Handles the two common shapes: a plain `conversation` and an
 * `extendedTextMessage` (text with context, e.g. replies/links).
 * @returns {string} the text, or '' if there is none.
 */
function extractText(message) {
  if (!message) return '';
  return (
    message.conversation ||
    message.extendedTextMessage?.text ||
    ''
  ).trim();
}

/**
 * Append a message to a thread's buffer and (re)arm its debounce timer.
 * The timer is reset on every new message so we flush only after WA_WINDOW_MS
 * of silence in that specific thread.
 */
function bufferMessage(remoteJid, text) {
  if (!buffers.has(remoteJid)) buffers.set(remoteJid, []);
  buffers.get(remoteJid).push(text);

  // Reset the debounce timer for this thread.
  const existing = timers.get(remoteJid);
  if (existing) clearTimeout(existing);

  timers.set(
    remoteJid,
    setTimeout(() => flushThread(remoteJid), WA_WINDOW_MS)
  );

  console.log(
    `[buffer] ${remoteJid}: +1 message (${buffers.get(remoteJid).length} buffered, ` +
      `flush in ${Math.round(WA_WINDOW_MS / 1000)}s)`
  );
}

/**
 * Flush a single thread: POST its buffered messages to the ingest endpoint,
 * log the {kept, stored} response, then clear the buffer and timer.
 */
async function flushThread(remoteJid) {
  const texts = buffers.get(remoteJid);

  // Clear state up front so new messages arriving during the POST start a
  // fresh window rather than being lost or double-sent.
  buffers.delete(remoteJid);
  const timer = timers.get(remoteJid);
  if (timer) clearTimeout(timer);
  timers.delete(remoteJid);

  if (!texts || texts.length === 0) return;

  // The endpoint expects a chat-style messages array. Each buffered WhatsApp
  // message becomes a "user" turn.
  const body = {
    messages: texts.map((content) => ({ role: 'user', content })),
    thread_id: remoteJid,
    sender: remoteJid,
  };

  console.log(`[flush] ${remoteJid}: posting ${texts.length} message(s)...`);

  try {
    const res = await fetch(`${WIKI_API_URL}/ingest/whatsapp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${WIKI_AUTH_TOKEN}`,
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      console.error(`[flush] ${remoteJid}: HTTP ${res.status} ${res.statusText} ${text}`);
      return;
    }

    const data = await res.json().catch(() => ({}));
    console.log(
      `[flush] ${remoteJid}: ok kept=${data.kept ?? '?'} stored=${data.stored ?? '?'}`
    );
  } catch (err) {
    console.error(`[flush] ${remoteJid}: request failed:`, err?.message ?? err);
  }
}

// ---------------------------------------------------------------------------
// WhatsApp connection.
// ---------------------------------------------------------------------------

async function start() {
  // Persist auth/session under ./auth so we only scan the QR once.
  const { state, saveCreds } = await useMultiFileAuthState('./auth');

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false, // we print it ourselves for clearer control
  });

  // Persist credentials whenever they change.
  sock.ev.on('creds.update', saveCreds);

  // Connection lifecycle: QR display + reconnect handling.
  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\n[wa] Scan this QR code with WhatsApp (Linked Devices):\n');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'open') {
      console.log('[wa] Connected. Listening for personal messages...');
    }

    if (connection === 'close') {
      // Baileys wraps disconnect errors as Boom errors, which expose the
      // status code at error.output.statusCode.
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;

      console.log(
        `[wa] Connection closed (code=${statusCode ?? 'n/a'}).` +
          (loggedOut ? ' Logged out — not reconnecting.' : ' Reconnecting...')
      );

      if (!loggedOut) {
        start().catch((err) => console.error('[wa] Reconnect failed:', err));
      }
    }
  });

  // Incoming messages.
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    // 'notify' is the type for freshly received messages (vs. history sync).
    if (type !== 'notify') return;

    for (const msg of messages) {
      const remoteJid = msg.key?.remoteJid;
      if (!remoteJid) continue;

      // Skip our own outgoing messages.
      if (msg.key.fromMe) continue;

      // Personal chats only: skip groups (@g.us) and status broadcasts.
      if (remoteJid.endsWith('@g.us')) continue;
      if (remoteJid === 'status@broadcast') continue;

      const text = extractText(msg.message);
      if (!text) continue; // ignore non-text (media, reactions, etc.)

      bufferMessage(remoteJid, text);
    }
  });
}

// Flush everything on graceful shutdown so buffered messages aren't lost.
async function shutdown() {
  console.log('\n[wa] Shutting down — flushing buffered threads...');
  const jids = [...buffers.keys()];
  await Promise.all(jids.map((jid) => flushThread(jid)));
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

start().catch((err) => {
  console.error('[wa] Fatal startup error:', err);
  process.exit(1);
});
