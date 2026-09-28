import { useEffect, useState } from "react";
import { AlertTriangle, LoaderCircle, RotateCcw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { RecoveryState } from "@/lib/types";

interface RecoveryNoticeProps {
  state: RecoveryState;
  onContinue: () => Promise<void>;
  onDismiss: () => Promise<void>;
}

export function RecoveryNotice({ state, onContinue, onDismiss }: RecoveryNoticeProps) {
  const { t } = useTranslation();
  const [pending, setPending] = useState<"continue" | "dismiss" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hiddenRecoveryId, setHiddenRecoveryId] = useState<string | null>(null);
  const [autoSpentId, setAutoSpentId] = useState<string | null>(null);

  const waiting = state.status === "awaiting_user" || state.status === "failed";
  const contextUnavailable = state.can_continue === false;
  // House ruling (Evans, 2026-09-28): routine interruptions never interrupt
  // the human. A planned restart or a blip with intact saved context just
  // continues — the notice is a quiet status line, not a decision. The
  // decision surface only appears when a person is genuinely needed: the
  // recovery failed, the saved context is unavailable, or the automatic
  // continuation itself failed (one automatic attempt per interruption).
  const canAuto = waiting && state.status !== "failed" && !contextUnavailable;
  const willAuto = canAuto && !error && autoSpentId !== state.recovery_id;

  const run = (action: "continue" | "dismiss") => {
    setPending(action);
    setError(null);
    // ``resuming`` is an internal transition, not another task for the user
    // to monitor. Hide the notice optimistically and only bring it back if
    // the explicit action is rejected.
    if (action === "continue") setHiddenRecoveryId(state.recovery_id);
    const operation = action === "continue" ? onContinue() : onDismiss();
    void operation.catch(() => {
      if (action === "continue") setHiddenRecoveryId(null);
      setError(t("recovery.actionFailed", { defaultValue: "Recovery action failed. Try again." }));
    }).finally(() => setPending(null));
  };

  useEffect(() => {
    // A continuation can be interrupted again with the same recovery ID.
    // Reveal the decision surface when the server returns to a waiting state
    // after the single automatic attempt has already been spent.
    if (state.status === "awaiting_user" || state.status === "failed") {
      setHiddenRecoveryId(null);
    }
  }, [state.recovery_id, state.status]);

  useEffect(() => {
    if (!willAuto || pending !== null) return;
    setAutoSpentId(state.recovery_id);
    run("continue");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [willAuto, state.recovery_id]);

  if (state.status === "recovered" || hiddenRecoveryId === state.recovery_id) return null;

  const quiet = state.status === "resuming" || (canAuto && !error && autoSpentId !== state.recovery_id);
  const title = state.status === "failed"
    ? t("recovery.failed", { defaultValue: "Task recovery failed" })
    : waiting && !quiet
      ? t("recovery.interrupted", { defaultValue: "Task interrupted" })
      : t("recovery.resuming", { defaultValue: "Restoring interrupted task…" });
  const detail = state.status === "failed" || contextUnavailable
    ? t("recovery.failedHelp", {
        defaultValue: "The saved task could not be restored safely. Review it before continuing.",
      })
    : waiting && !quiet
      ? t("recovery.review", { defaultValue: "Review the task before continuing. Tools will not be replayed automatically." })
      : t("recovery.safeResume", { defaultValue: "Continuing from saved conversation context." });

  return (
    <div
      role={waiting && !quiet ? "alert" : "status"}
      aria-live={waiting && !quiet ? "assertive" : "polite"}
      aria-busy={state.status === "resuming" || quiet}
      data-recovery-status={state.status}
      className="mx-auto mb-2 flex w-full max-w-[49.5rem] items-center gap-3 rounded-control border border-border/70 bg-muted/35 px-3 py-2 text-sm transition-[background-color,border-color,opacity,transform] duration-200 ease-out motion-reduce:transition-none animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none"
    >
      {waiting && !quiet ? (
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
      ) : (
        <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          {title}
        </p>
        <p className={cn(
          "mt-0.5 text-xs",
          error ? "text-destructive" : "text-muted-foreground",
        )}>
          {error ?? detail}
        </p>
      </div>
      {waiting && !quiet ? (
        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={pending !== null}
            onClick={() => run("dismiss")}
          >
            <X className="mr-1 h-3.5 w-3.5" aria-hidden />
            {t("recovery.dismiss", { defaultValue: "Dismiss" })}
          </Button>
          {!contextUnavailable ? (
            <Button
              type="button"
              size="sm"
              disabled={pending !== null}
              onClick={() => run("continue")}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden />
              {t("recovery.continue", { defaultValue: "Continue" })}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
