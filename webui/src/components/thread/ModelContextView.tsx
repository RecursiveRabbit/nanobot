import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  fetchSessionContext,
  type SessionContextPayload,
  type SessionContextReplayMessage,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface ModelContextViewProps {
  sessionKey: string;
  token: string;
}

/**
 * The shared window: exactly what the model will see alongside the next
 * prompt — the working-notes checkpoint plus the uncovered replay tail.
 * Read-only by design; the chat view remains the only way to send.
 */
export function ModelContextView({ sessionKey, token }: ModelContextViewProps) {
  const { t } = useTranslation();
  const [payload, setPayload] = useState<SessionContextPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPayload(await fetchSessionContext(token, sessionKey, { full: true }));
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [sessionKey, token]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div
      data-testid="model-context-view"
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-2">
        <div className="min-w-0 truncate text-[12px] text-muted-foreground">
          {payload
            ? t("modelView.stats", {
                archived: payload.archived_messages,
                replay: payload.replay_messages,
                tokens: payload.estimated_session_tokens,
              })
            : null}
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("modelView.refresh")}
          onClick={() => void load()}
          className="h-7 w-7 rounded-md text-muted-foreground hover:bg-accent/35 hover:text-foreground"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {loading && !payload ? (
          <div className="py-8 text-center text-[13px] text-muted-foreground">
            {t("modelView.loading")}
          </div>
        ) : null}
        {error ? (
          <div className="py-8 text-center text-[13px] text-destructive">
            {t("modelView.error")}: {error}
          </div>
        ) : null}
        {payload ? (
          <div className="mx-auto flex max-w-3xl flex-col gap-3">
            <section>
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("modelView.notes")}
              </div>
              {payload.archived_summary ? (
                <pre className="whitespace-pre-wrap rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 font-sans text-[13px]/[1.5]">
                  {payload.archived_summary}
                </pre>
              ) : (
                <div className="rounded-lg border border-dashed border-border/70 p-3 text-[13px] text-muted-foreground">
                  {t("modelView.noNotes")}
                </div>
              )}
            </section>

            <section>
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("modelView.replay")}
              </div>
              <div className="flex flex-col gap-2">
                {(payload.replay ?? []).map((message, index) => (
                  <ReplayRow key={index} message={message} />
                ))}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ReplayRow({ message }: { message: SessionContextReplayMessage }) {
  const { t } = useTranslation();

  if (message.checkpoint) {
    return (
      <div className="flex items-center gap-2 py-1" data-testid="model-context-boundary">
        <div className="h-px flex-1 bg-amber-500/40" />
        <div className="text-[11px] text-amber-600 dark:text-amber-400">
          {t("modelView.boundary")}
        </div>
        <div className="h-px flex-1 bg-amber-500/40" />
      </div>
    );
  }

  const roleKey =
    message.role === "user"
      ? "modelView.roleUser"
      : message.role === "assistant"
        ? "modelView.roleAssistant"
        : message.role === "tool"
          ? "modelView.roleTool"
          : "modelView.roleSystem";

  return (
    <div className="rounded-lg border border-border/60 p-3">
      <div className="mb-1 text-[11px] font-medium text-muted-foreground">
        {t(roleKey)}
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans text-[13px]/[1.5]">
        {message.content}
      </pre>
    </div>
  );
}
