/**
 * Contact delivery through Formspree (https://formspree.io), an external form-to-email service.
 * Only the public form ID lives in the browser (VITE_FORMSPREE_FORM_ID); the destination address,
 * spam filtering and server-side rate limiting are configured in the Formspree account — no private
 * secret is ever shipped to the client. Formspree also honours the "_gotcha" honeypot field.
 */
export interface ContactPayload {
  firstName: string;
  lastName: string;
  email: string;
  phone?: string;
  message: string;
  /** honeypot: must stay empty */
  website?: string;
}

export type ContactResult =
  | { ok: true }
  | { ok: false; reason: 'unconfigured' | 'rate_limited' | 'rejected' | 'network' | 'spam'; message: string };

const FORM_ID = (import.meta.env.VITE_FORMSPREE_FORM_ID as string | undefined)?.trim() ?? '';
const MIN_INTERVAL_MS = 30_000;
let lastSentAt = 0;

export const contactConfigured = () => /^[A-Za-z0-9]{6,20}$/.test(FORM_ID);

export async function sendContactMessage(p: ContactPayload, now = Date.now()): Promise<ContactResult> {
  if (p.website) {
    // Honeypot filled in: a bot. Do not send, and do not claim success.
    return { ok: false, reason: 'spam', message: 'Your message could not be sent.' };
  }
  if (!contactConfigured()) {
    return {
      ok: false,
      reason: 'unconfigured',
      message:
        'Message not sent: the contact service is not configured on this deployment yet. Your text has been kept so you can try again later.',
    };
  }
  if (now - lastSentAt < MIN_INTERVAL_MS) {
    return {
      ok: false,
      reason: 'rate_limited',
      message: 'Please wait a little before sending another message.',
    };
  }
  let res: Response;
  try {
    res = await fetch(`https://formspree.io/f/${FORM_ID}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        name: `${p.firstName} ${p.lastName}`.trim(),
        email: p.email,
        phone: p.phone || undefined,
        message: p.message,
        _subject: 'FireHarmonics contact form',
        _gotcha: p.website ?? '',
      }),
    });
  } catch {
    return { ok: false, reason: 'network', message: 'The message could not be sent because the network request failed.' };
  }
  if (res.ok) {
    lastSentAt = now;
    return { ok: true };
  }
  if (res.status === 429) {
    return { ok: false, reason: 'rate_limited', message: 'Too many messages were sent recently. Please try again later.' };
  }
  let detail = '';
  try {
    const body = (await res.json()) as { errors?: { message?: string }[]; error?: string };
    detail = body.errors?.map((e) => e.message).filter(Boolean).join(' ') || body.error || '';
  } catch {
    /* ignore */
  }
  return {
    ok: false,
    reason: 'rejected',
    message: `The message was not accepted (HTTP ${res.status})${detail ? `: ${detail}` : '.'}`,
  };
}
