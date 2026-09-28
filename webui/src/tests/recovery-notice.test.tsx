import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecoveryNotice } from "@/components/thread/RecoveryNotice";

const INTERRUPTED = {
  status: "awaiting_user" as const,
  recovery_id: "recovery-1",
  reason: "tool_state_uncertain",
};

describe("RecoveryNotice", () => {
  it("auto-continues a routine interruption without asking the human", async () => {
    const onContinue = vi.fn().mockResolvedValue(undefined);

    render(
      <RecoveryNotice
        state={INTERRUPTED}
        onContinue={onContinue}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    // House ruling (Evans 2026-09-28): no decision surface, no click — the
    // machine resumes itself and only ever shows a quiet status line.
    await waitFor(() => expect(onContinue).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
  });

  it("uses the shared status surface and motion treatment", () => {
    render(
      <RecoveryNotice
        state={{ status: "resuming", recovery_id: "recovery-1" }}
        onContinue={vi.fn().mockResolvedValue(undefined)}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const notice = screen.getByRole("status");
    expect(notice).toHaveAttribute("data-recovery-status", "resuming");
    expect(notice).toHaveAttribute("aria-live", "polite");
    expect(notice).toHaveClass(
      "max-w-[49.5rem]",
      "rounded-control",
      "animate-in",
      "fade-in-0",
      "slide-in-from-bottom-1",
      "duration-200",
      "motion-reduce:animate-none",
    );
  });

  it("shows the decision surface when the automatic continuation fails", async () => {
    const onContinue = vi.fn().mockRejectedValue(new Error("offline"));

    render(
      <RecoveryNotice
        state={INTERRUPTED}
        onContinue={onContinue}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Recovery action failed");
    });
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it("shows the decision surface again when a continuation is re-interrupted", async () => {
    const onContinue = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <RecoveryNotice
        state={INTERRUPTED}
        onContinue={onContinue}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    // First interruption: the automatic attempt fires and is spent.
    await waitFor(() => expect(onContinue).toHaveBeenCalledOnce());

    rerender(
      <RecoveryNotice
        state={{ status: "resuming", recovery_id: "recovery-1" }}
        onContinue={onContinue}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    rerender(
      <RecoveryNotice
        state={{ ...INTERRUPTED, reason: "loop_guard" }}
        onContinue={onContinue}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    // One automatic attempt per interruption, then a person decides — the
    // loop guard case never spins the machine.
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it("does not offer Continue when saved conversation context is unavailable", () => {
    render(
      <RecoveryNotice
        state={{ ...INTERRUPTED, can_continue: false }}
        onContinue={vi.fn().mockResolvedValue(undefined)}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("keeps the failure surface for failed recovery states", () => {
    render(
      <RecoveryNotice
        state={{ status: "failed", recovery_id: "recovery-1", reason: "checkpoint_missing" }}
        onContinue={vi.fn().mockResolvedValue(undefined)}
        onDismiss={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Task recovery failed");
  });
});
