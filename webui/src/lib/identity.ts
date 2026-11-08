/** WebUI profile identity (Settings → Identity).
 *
 * The server issues a stable client id per WebUI profile in the ``ready``
 * frame; the client persists it and presents it on every connect so the
 * server can attach the operator-selected display name to new messages.
 * The display name itself never lives here — it is server-persisted and
 * read back through the identity mutations, so a refresh, restart, or new
 * tab always sees the authoritative value.
 */

import type { UIMessageOriginator } from "@/lib/types";

const PROFILE_CLIENT_ID_KEY = "nanobot.profile.client_id";
const PROFILE_ORIGINATOR_KEY = "nanobot.profile.originator";

export function loadProfileClientId(): string | null {
  try {
    const value = window.localStorage.getItem(PROFILE_CLIENT_ID_KEY);
    return value && value.trim() ? value.trim() : null;
  } catch {
    return null;
  }
}

export function saveProfileClientId(clientId: string | null): void {
  try {
    if (clientId && clientId.trim()) {
      window.localStorage.setItem(PROFILE_CLIENT_ID_KEY, clientId.trim());
    } else {
      window.localStorage.removeItem(PROFILE_CLIENT_ID_KEY);
    }
  } catch {
    // Storage unavailable (private mode, embed): the profile simply stays
    // per-connection, which the server treats as anonymous until named.
  }
}

/** Cache the originator the server resolved for this profile at connect. */
export function cacheProfileOriginator(originator: UIMessageOriginator | null): void {
  try {
    if (originator) {
      window.localStorage.setItem(PROFILE_ORIGINATOR_KEY, JSON.stringify(originator));
    } else {
      window.localStorage.removeItem(PROFILE_ORIGINATOR_KEY);
    }
  } catch {
    // Non-fatal: optimistic echo labels fall back to no label.
  }
}

/** Optimistic originator for locally echoed messages, pending the server's
 * authoritative ``user_message`` broadcast. Absent/invalid → anonymous. */
export function loadCachedOriginator(): UIMessageOriginator {
  try {
    const raw = window.localStorage.getItem(PROFILE_ORIGINATOR_KEY);
    if (!raw) return { kind: "anonymous" };
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed
      && typeof parsed === "object"
      && ["anonymous", "human", "resident", "system"].includes(
        String((parsed as { kind?: unknown }).kind),
      )
    ) {
      return parsed as UIMessageOriginator;
    }
  } catch {
    // fall through
  }
  return { kind: "anonymous" };
}

/** Short display label for an originator; anonymous stays explicit. */
export function originatorLabel(originator: UIMessageOriginator | null | undefined): string | null {
  if (!originator) return null;
  if (originator.kind === "anonymous") return null; // caller renders localized "Anonymous"
  const label = (originator.label || "").trim();
  if (label) return label;
  const handle = (originator.handle || "").trim();
  if (handle) return `@${handle}`;
  return null;
}
