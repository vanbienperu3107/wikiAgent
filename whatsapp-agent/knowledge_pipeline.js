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
 *   - Personal chats only: groups (@g.us), status/broadcasts (@broadcast) and
 *     WhatsApp Channels (@newsletter) are skipped.
 *   - Self-authored messages (key.fromMe) are skipped.
 *   - Per-thread debounce: WA_WINDOW_MS after the LAST message in a thread, flush it.
 *   - Fully in-memory: a Map of buffers + a Map of debounce timers. No database.
 *   - A failed POST keeps the buffer so the next window retries it; buffers are
 *     capped to avoid unbounded growth when the endpoint is down.
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

// Cap on how many messages a single thread may buffer before we start dropping
// the oldest. Protects memory when the ingest endpoint is unavailable and
// failed flushes keep the buffer around.
const MAX_THREAD_BUFFER = 500;

// Reconnect backoff bounds.
const RECONNECT_BASE_MS = 1000; // first retry delay
const RECONNECT_MAX_MS = 60000; // cap

// Overall timeout for the graceful-shutdown flush so a hung endpoint can't
// block process exit.
const SHUTDOWN_FLUSH_TIMEOUT_MS = 10000;

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
  console.error('[config] Global fetch is unavailable. Use Node 18+.');
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
 * Mask a WhatsApp JID for logging so we never emit a full contact phone number.
 * Keeps only a short suffix of the local part, e.g. `***1234@s.whatsapp.net`.
 * @param {string} [jid]
 * @returns {string}
 */
function maskJid(jid) {
  if (!jid) return '(unknown)';
  const at = jid.indexOf('@');
  const local = at === -1 ? jid : jid.slice(0, at);
  const domain = at === -1 ? '' : jid.slice(at);
  const suffix = local.slice(-4);
  return `***${suffix}${domain}`;
}

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
  const buf = buffers.get(remoteJid);
  buf.push(text);

  // Guard against unbounded growth (e.g. when flushes keep failing): drop the
  // oldest messages once we exceed the cap.
  if (buf.length > MAX_THREAD_BUFFER) {
    const dropped = buf.length - MAX_THREAD_BUFFER;
    buf.splice(0, dropped);
    console.warn(
      `[buffer] ${maskJid(remoteJid)}: buffer over cap, dropped ${dropped} oldest message(s)`
    );
  }

  // Reset the debounce timer for this thread.
  const existing = timers.get(remoteJid);
  if (existing) clearTimeout(existing);

  timers.set(
    remoteJid,
    setTimeout(() => {
      flushThread(remoteJid).catch((err) =>
        console.error(`[flush] ${maskJid(remoteJid)}: unexpected error:`, err?.message ?? err)
      );
    }, WA_WINDOW_MS)
  );

  console.log(
    `[buffer] ${maskJid(remoteJid)}: +1 message (${buf.length} buffered, ` +
      `flush in ${Math.round(WA_WINDOW_MS / 1000)}s)`
  );
}

/**
 * Flush a single thread: POST its buffered messages to the ingest endpoint,
 * log the {kept, stored} response, then clear the buffer and timer.
 *
 * The buffer is only cleared AFTER a successful (res.ok) POST. On any failure
 * the buffer is kept so the next debounce window (or shutdown) retries it,
 * ensuring a transient error can't silently drop messages.
 */
async function flushThread(remoteJid) {
  const texts = buffers.get(remoteJid);
  if (!texts || texts.length === 0) {
    // Nothing to send — clean up any lingering timer.
    const t = timers.get(remoteJid);
    if (t) clearTimeout(t);
    timers.delete(remoteJid);
    buffers.delete(remoteJid);
    return;
  }

  // Snapshot the messages being sent. New messages that arrive during the POST
  // are appended to the live buffer and are handled by a subsequent flush.
  const snapshot = texts.slice();

  // The endpoint expects a chat-style messages array. Each buffered WhatsApp
  // message becomes a "user" turn.
  const body = {
    messages: snapshot.map((content) => ({ role: 'user', content })),
    thread_id: remoteJid,
    sender: remoteJid,
  };

  console.log(`[flush] ${maskJid(remoteJid)}: posting ${snapshot.length} message(s)...`);

  let res;
  try {
    res = await fetch(`${WIKI_API_URL}/ingest/whatsapp`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${WIKI_AUTH_TOKEN}`,
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.warn(
      `[flush] ${maskJid(remoteJid)}: request failed, keeping buffer for retry:`,
      err?.message ?? err
    );
    return; // keep buffer + timer intact for the next attempt
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    console.warn(
      `[flush] ${maskJid(remoteJid)}: HTTP ${res.status} ${res.statusText} ${detail} ` +
        `— keeping buffer for retry`
    );
    return; // keep buffer + timer intact for the next attempt
  }

  const data = await res.json().catch(() => ({}));
  console.log(
    `[flush] ${maskJid(remoteJid)}: ok kept=${data.kept ?? '?'} stored=${data.stored ?? '?'}`
  );

  // Success: remove exactly the messages we sent. Anything appended during the
  // POST is preserved so we don't lose in-flight arrivals.
  const current = buffers.get(remoteJid);
  if (current) {
    current.splice(0, snapshot.length);
    if (current.length === 0) {
      buffers.delete(remoteJid);
      const t = timers.get(remoteJid);
      if (t) clearTimeout(t);
      timers.delete(remoteJid);
    }
    // If messages remain, the existing debounce timer (or a new one armed by
    // bufferMessage) will flush them.
  }
}

// ---------------------------------------------------------------------------
// WhatsApp connection.
// ---------------------------------------------------------------------------

let reconnectDelay = RECONNECT_BASE_MS;

/** Returns true for JIDs we should NOT ingest (non 1:1 personal chats). */
function isNonPersonalJid(remoteJid) {
  return (
    remoteJid.endsWith('@g.us') ||
    remoteJid.endsWith('@newsletter') ||
    remoteJid.endsWith('@broadcast') ||
    remoteJid === 'status@broadcast'
  );
}

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
      reconnectDelay = RECONNECT_BASE_MS; // reset backoff on a healthy connection
    }

    if (connection === 'close') {
      // Baileys wraps disconnect errors as Boom errors, which expose the
      // status code at error.output.statusCode.
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;

      if (loggedOut) {
        console.log(`[wa] Connection closed (code=${statusCode ?? 'n/a'}). Logged out — not reconnecting.`);
        return;
      }

      // Exponential backoff with jitter to avoid a tight reconnect loop.
      const jitter = Math.floor(Math.random() * 1000);
      const delay = Math.min(reconnectDelay, RECONNECT_MAX_MS) + jitter;
      console.log(
        `[wa] Connection closed (code=${statusCode ?? 'n/a'}). Reconnecting in ${Math.round(delay / 1000)}s...`
      );

      setTimeout(() => {
        start().catch((err) => console.error('[wa] Reconnect failed:', err?.message ?? err));
      }, delay);

      // Grow the delay for the next attempt (before jitter), capped.
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    }
  });

  // Incoming messages.
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    // 'notify' is the type for freshly received messages (vs. history sync).
    if (type !== 'notify') return;

    for (const msg of messages) {
      // Guard each message individually so one malformed payload can't crash
      // the socket / take down the whole handler.
      try {
        const remoteJid = msg.key?.remoteJid;
        if (!remoteJid) continue;

        // Skip our own outgoing messages.
        if (msg.key.fromMe) continue;

        // Personal 1:1 chats only: skip groups, channels and broadcasts.
        if (isNonPersonalJid(remoteJid)) continue;

        const text = extractText(msg.message);
        if (!text) continue; // ignore non-text (media, reactions, etc.)

        bufferMessage(remoteJid, text);
      } catch (err) {
        console.error('[wa] Failed to process a message:', err?.message ?? err);
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Flush-all + shutdown / crash safety.
// ---------------------------------------------------------------------------

/** Flush every buffered thread. Failures are kept for retry by flushThread. */
async function flushAll() {
  const jids = [...buffers.keys()];
  await Promise.all(
    jids.map((jid) =>
      flushThread(jid).catch((err) =>
        console.error(`[flush] ${maskJid(jid)}: flush error:`, err?.message ?? err)
      )
    )
  );
}

/**
 * Best-effort flush bounded by an overall timeout so a hung ingest endpoint
 * can't block process exit.
 */
function flushAllWithTimeout(timeoutMs) {
  return Promise.race([
    flushAll(),
    new Promise((resolve) =>
      setTimeout(() => {
        console.warn(`[wa] Flush timed out after ${Math.round(timeoutMs / 1000)}s — exiting anyway.`);
        resolve();
      }, timeoutMs)
    ),
  ]);
}

// Flush everything on graceful shutdown so buffered messages aren't lost.
let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`\n[wa] ${signal ?? 'shutdown'} — flushing buffered threads...`);
  await flushAllWithTimeout(SHUTDOWN_FLUSH_TIMEOUT_MS);
  process.exit(0);
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

// Crash safety: attempt a bounded best-effort flush before exiting so an
// unexpected error doesn't silently drop buffered messages.
process.on('uncaughtException', async (err) => {
  console.error('[wa] Uncaught exception:', err?.stack ?? err);
  try {
    await flushAllWithTimeout(SHUTDOWN_FLUSH_TIMEOUT_MS);
  } finally {
    process.exit(1);
  }
});

process.on('unhandledRejection', async (reason) => {
  console.error('[wa] Unhandled rejection:', reason?.stack ?? reason);
  try {
    await flushAllWithTimeout(SHUTDOWN_FLUSH_TIMEOUT_MS);
  } finally {
    process.exit(1);
  }
});

start().catch((err) => {
  console.error('[wa] Fatal startup error:', err);
  process.exit(1);
});
