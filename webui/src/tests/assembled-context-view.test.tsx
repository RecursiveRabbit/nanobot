import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssembledContextView } from "@/components/thread/AssembledContextView";

const PAYLOAD = {
  schema_version: 1,
  session_key: "websocket:chat-1",
  model: "test-model",
  provider: "test",
  provider_state_resumable: false,
  messages: [
    { role: "system", content: "SYSTEM PROMPT BODY" },
    { role: "user", content: "tail user message" },
  ],
  message_flags: [{ checkpoint: false }, { checkpoint: false }],
  tools: [{ function: { name: "exec", description: "Run a command." } }],
};

describe("AssembledContextView", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => PAYLOAD,
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the complete system prompt and untruncated replay", async () => {
    render(<AssembledContextView sessionKey="websocket:chat-1" token="tok" />);
    await waitFor(() => screen.getByText("tail user message"));
    expect(screen.getByText("System prompt")).toBeInTheDocument();
    expect(screen.getByText(/Tool definitions/)).toBeInTheDocument();
  });

  it("previews the draft merged into a trailing user message (wire form)", async () => {
    render(
      <AssembledContextView
        sessionKey="websocket:chat-1"
        token="tok"
        draftText="typed just now"
      />,
    );
    await waitFor(() => screen.getByTestId("assembled-pending-send"));
    const pending = screen.getByTestId("assembled-pending-send");
    // The wire merges with "\n\n"; the preview must show that exact text.
    expect(pending.textContent).toContain("tail user message\n\ntyped just now");
  });

  it("previews the draft as a new message when the tail is assistant", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        ...PAYLOAD,
        messages: [
          PAYLOAD.messages[0],
          { role: "assistant", content: "assistant tail" },
        ],
        message_flags: [{ checkpoint: false }, { checkpoint: false }],
      }),
    }));
    render(
      <AssembledContextView
        sessionKey="websocket:chat-1"
        token="tok"
        draftText="typed just now"
      />,
    );
    await waitFor(() => screen.getByTestId("assembled-pending-send"));
    const pending = screen.getByTestId("assembled-pending-send");
    expect(pending.textContent).toContain("typed just now");
    expect(pending.textContent).not.toContain("assistant tail");
  });
});
