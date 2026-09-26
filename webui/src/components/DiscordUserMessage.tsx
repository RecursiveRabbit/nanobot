import { UserMessageText } from "@/components/UserMessageText";
import { parseDiscordMessage } from "@/lib/discord-format";
import type { CliAppInfo, McpPresetInfo, SessionMention } from "@/lib/types";

/**
 * Discord-originated user messages: the machine header becomes a byline,
 * <@id> tokens become readable names, the body renders as usual.
 * Falls back to the plain renderer for non-Discord content.
 */
export function DiscordUserMessage({
  text,
  cliApps,
  mcpPresets,
  sessionMentions = [],
}: {
  text: string;
  cliApps: CliAppInfo[];
  mcpPresets: McpPresetInfo[];
  sessionMentions?: SessionMention[];
}) {
  const parsed = parseDiscordMessage(text);
  if (!parsed) {
    return (
      <UserMessageText
        text={text}
        cliApps={cliApps}
        mcpPresets={mcpPresets}
        sessionMentions={sessionMentions}
      />
    );
  }

  return (
    <div className="flex flex-col gap-1" data-testid="discord-message">
      <div
        className="text-[11px] leading-4 text-muted-foreground"
        data-testid="discord-byline"
      >
        {parsed.guild} &gt; {parsed.channel} · {parsed.authorName} · {parsed.timestamp}
      </div>
      <div>
        <UserMessageText
          text={parsed.body}
          cliApps={cliApps}
          mcpPresets={mcpPresets}
          sessionMentions={sessionMentions}
        />
      </div>
    </div>
  );
}
