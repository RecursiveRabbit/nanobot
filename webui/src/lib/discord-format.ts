/** Discord inbound envelope parsing for the channel-session window.
 *
 * The discord channel prefixes inbound user messages with a machine header
 *   [discord · Guild > #channel · Name (@handle, id) · timestamp · msg id]
 * optionally followed by a mentions trailer
 *   [mentions: Name (id), Name (id)]
 * and a body that carries <@id> mention tokens. The header is data, not
 * instructions; we render it as a byline and make mentions readable.
 */

export interface DiscordMessageParts {
  guild: string;
  channel: string;
  authorName: string;
  authorHandle: string;
  authorId: string;
  timestamp: string;
  msgId: string;
  body: string;
}

const HEADER_RE =
  /^\[discord · ([^[\]]+?) > (#[^·]+?) · ([^·]+?) \(@([^,]+), (\d+)\) · ([^·]+?) · msg (\d+)\]\n?/;

const MENTIONS_RE = /^\[mentions: ([^\]]+)\]\n?/;

const MENTION_TOKEN_RE = /<@!?(\d+)>/g;

export function parseDiscordMessage(text: string): DiscordMessageParts | null {
  const header = HEADER_RE.exec(text);
  if (!header) return null;

  let rest = text.slice(header[0].length);
  const mentions = new Map<string, string>();
  const trailer = MENTIONS_RE.exec(rest);
  if (trailer) {
    rest = rest.slice(trailer[0].length);
    for (const part of trailer[1].split(/,\s*/)) {
      const entry = /^\s*(.+?)\s*\((\d+)\)\s*$/.exec(part);
      if (entry) mentions.set(entry[2], entry[1]);
    }
  }

  const body = rest.replace(
    MENTION_TOKEN_RE,
    (_token, id: string) => `@${mentions.get(id) ?? id}`,
  );

  return {
    guild: header[1].trim(),
    channel: header[2].trim(),
    authorName: header[3].trim(),
    authorHandle: header[4],
    authorId: header[5],
    timestamp: header[6].trim(),
    msgId: header[7],
    body,
  };
}
