import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  fetchAssembledContext,
  type AssembledContextMessage,
  type AssembledContextPayload,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface AssembledContextViewProps {
  sessionKey: string;
  token: string;
  /** Bumped when a turn ends; refreshes the assembly. */
  refreshKey?: unknown;
  /** Live composer draft — rendered in its wire form so the screen matches
   * what pressing enter sends, including the tail-merge rule. */
  draftText?: string;
}

function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) =>
        block && typeof block === "object" && "text" in block
          ? String((block as { text: unknown }).text)
          : "[non-text block]",
      )
      .join("\n");
  }
  return content == null ? "" : String(content);
}

/**
 * The shared window: the byte-exact context the next turn sends to the model.
 * Completeness is the contract; every section renders in full when open.
 */
export function AssembledContextView({
  sessionKey,
  token,
  refreshKey,
  draftText,
}: AssembledContextViewProps) {
  const { t } = useTranslation();
  const [payload, setPayload] = useState<AssembledContextPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPayload(await fetchAssembledContext(token, sessionKey));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [sessionKey, token]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  return (
    <div
      data-testid="assembled-context-view"
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-2">
        <div className="min-w-0 truncate text-[12px] text-muted-foreground">
          {payload
            ? t("assembledView.stats", {
                model: payload.model,
                messages: payload.messages.length,
                tools: payload.tools.length,
              })
            : null}
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("assembledView.refresh")}
          onClick={() => void load()}
          className="h-7 w-7 rounded-md text-muted-foreground hover:bg-accent/35 hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {error ? (
          <div className="py-8 text-center text-[13px] text-destructive">
            {t("assembledView.error")}: {error}
          </div>
        ) : null}
        {!payload && loading ? (
          <div className="py-8 text-center text-[13px] text-muted-foreground">
            {t("assembledView.loading")}
          </div>
        ) : null}
        {payload ? (
          <div className="mx-auto flex max-w-3xl flex-col gap-3">
            {payload.provider_state_resumable ? (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-[12px] text-amber-700 dark:text-amber-300">
                {t("assembledView.resumableNote")}
              </div>
            ) : null}
            <SystemSection payload={payload} />
            <ToolsSection payload={payload} />
            <McpSection payload={payload} />
            <section>
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("assembledView.messages")}
              </div>
              <div className="flex flex-col gap-2">
                {payload.messages.slice(1).map((message, index) => (
                  <MessageRow
                    key={index}
                    message={message}
                    checkpoint={payload.message_flags[index + 1]?.checkpoint === true}
                  />
                ))}
                {draftText?.trim() ? (
                  <PendingSendRow messages={payload.messages} draft={draftText} />
                ) : null}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Collapsible({
  title,
  bytes,
  children,
  defaultOpen = false,
}: {
  title: string;
  bytes?: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="rounded-lg border border-border/60">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] font-medium text-muted-foreground hover:text-foreground"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        )}
        <span>{title}</span>
        {bytes !== undefined ? (
          <span className="ml-auto text-[11px] tabular-nums">{bytes.toLocaleString()} B</span>
        ) : null}
      </button>
      {open ? <div className="border-t border-border/50 p-3">{children}</div> : null}
    </section>
  );
}

function SystemSection({ payload }: { payload: AssembledContextPayload }) {
  const { t } = useTranslation();
  const system = payload.messages[0];
  const text = messageText(system?.content);
  return (
    <Collapsible title={t("assembledView.systemPrompt")} bytes={text.length}>
      <pre className="whitespace-pre-wrap break-words font-sans text-[13px]/[1.5]">{text}</pre>
    </Collapsible>
  );
}

function ToolsSection({ payload }: { payload: AssembledContextPayload }) {
  const { t } = useTranslation();
  const full = useMemo(
    () => JSON.stringify(payload.tools, null, 2),
    [payload.tools],
  );
  return (
    <Collapsible
      title={t("assembledView.tools", { count: payload.tools.length })}
      bytes={full.length}
    >
      <div className="flex flex-col gap-2">
        {payload.tools.map((tool, index) => (
          <div key={index} className="rounded-md border border-border/50 p-2">
            <code className="text-[12px] font-medium">{tool.function?.name}</code>
            <div className="mt-0.5 whitespace-pre-wrap break-words text-[12px]/[1.45] text-muted-foreground">
              {tool.function?.description}
            </div>
          </div>
        ))}
      </div>
    </Collapsible>
  );
}

function McpSection({ payload }: { payload: AssembledContextPayload }) {
  const { t } = useTranslation();
  const status = payload.mcp_status;
  if (!status || Object.keys(status).length === 0) return null;
  return (
    <Collapsible title={t("assembledView.mcp")}>
      <div className="flex flex-col gap-1">
        {Object.entries(status).map(([name, state]) => (
          <div key={name} className="flex justify-between gap-2 text-[12px]">
            <code>{name}</code>
            <span className="text-muted-foreground">{state}</span>
          </div>
        ))}
      </div>
    </Collapsible>
  );
}

/**
 * The draft in its wire form. The transcript merges a typed message into a
 * trailing user message (`tail\n\ndraft`); the preview reproduces that merge
 * so what is on screen is what pressing enter sends.
 */
function PendingSendRow({
  messages,
  draft,
}: {
  messages: AssembledContextMessage[];
  draft: string;
}) {
  const { t } = useTranslation();
  const tail = messages.length > 1 ? messages[messages.length - 1] : null;
  const merges = tail?.role === "user";
  const wireText = merges
    ? `${messageText(tail.content)}\n\n${draft}`
    : draft;

  return (
    <div
      className="rounded-lg border border-dashed border-primary/50 p-3"
      data-testid="assembled-pending-send"
    >
      <div className="mb-1 text-[11px] font-medium text-primary">
        {t("assembledView.pendingSend")}
        {merges ? ` · ${t("assembledView.mergedIntoTail")}` : ""}
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans text-[13px]/[1.5]">
        {wireText}
      </pre>
    </div>
  );
}

function MessageRow({
  message,
  checkpoint,
}: {
  message: AssembledContextMessage;
  checkpoint: boolean;
}) {
  const { t } = useTranslation();

  if (checkpoint) {
    return (
      <div className="flex items-center gap-2 py-1" data-testid="assembled-boundary">
        <div className="h-px flex-1 bg-amber-500/40" />
        <div className="text-[11px] text-amber-600 dark:text-amber-400">
          {t("assembledView.boundary")}
        </div>
        <div className="h-px flex-1 bg-amber-500/40" />
      </div>
    );
  }

  const roleKey =
    message.role === "user"
      ? "assembledView.roleUser"
      : message.role === "assistant"
        ? "assembledView.roleAssistant"
        : message.role === "tool"
          ? "assembledView.roleTool"
          : "assembledView.roleSystem";

  return (
    <div className="rounded-lg border border-border/60 p-3">
      <div className="mb-1 text-[11px] font-medium text-muted-foreground">{t(roleKey)}</div>
      <pre className="whitespace-pre-wrap break-words font-sans text-[13px]/[1.5]">
        {messageText(message.content)}
      </pre>
    </div>
  );
}
