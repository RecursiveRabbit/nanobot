import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageBubble } from "@/components/MessageBubble";
import { IdentitySettings } from "@/components/settings/IdentitySettings";
import { setAppLanguage } from "@/i18n";
import { fetchIdentity, updateIdentity } from "@/lib/api";
import { deriveWsUrl } from "@/lib/bootstrap";
import {
  cacheProfileOriginator,
  loadCachedOriginator,
  loadProfileClientId,
  originatorLabel,
  saveProfileClientId,
} from "@/lib/identity";
import type { UIMessage } from "@/lib/types";

const { requestMutation } = vi.hoisted(() => ({ requestMutation: vi.fn() }));

vi.mock("@/providers/ClientProvider", () => {
  const client = { requestMutation };
  return { useClient: () => ({ client }) };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    fetchIdentity: vi.fn(),
    updateIdentity: vi.fn(),
  };
});

const mockedFetchIdentity = vi.mocked(fetchIdentity);
const mockedUpdateIdentity = vi.mocked(updateIdentity);

function userMessage(overrides: Partial<UIMessage> = {}): UIMessage {
  return {
    id: "m-1",
    role: "user",
    content: "hello",
    createdAt: Date.now(),
    ...overrides,
  };
}

beforeEach(async () => {
  await setAppLanguage("en");
  window.localStorage.clear();
  requestMutation.mockReset();
  mockedFetchIdentity.mockReset();
  mockedUpdateIdentity.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("profile identity storage", () => {
  it("persists the server-issued client id across refreshes", () => {
    expect(loadProfileClientId()).toBeNull();
    saveProfileClientId("anon-abc123");
    expect(loadProfileClientId()).toBe("anon-abc123");
    saveProfileClientId(null);
    expect(loadProfileClientId()).toBeNull();
  });

  it("caches the resolved originator and falls back to anonymous", () => {
    expect(loadCachedOriginator()).toEqual({ kind: "anonymous" });
    cacheProfileOriginator({ kind: "human", label: "Evans", attestation: "operator-profile" });
    expect(loadCachedOriginator()).toEqual({
      kind: "human",
      label: "Evans",
      attestation: "operator-profile",
    });
    cacheProfileOriginator(null);
    expect(loadCachedOriginator()).toEqual({ kind: "anonymous" });
  });

  it("labels named and resident originators, never invents one", () => {
    expect(originatorLabel({ kind: "anonymous" })).toBeNull();
    expect(originatorLabel(null)).toBeNull();
    expect(originatorLabel(undefined)).toBeNull();
    expect(
      originatorLabel({ kind: "human", label: "Evans", attestation: "operator-profile" }),
    ).toBe("Evans");
    expect(
      originatorLabel({
        kind: "resident",
        handle: "pore",
        attestation: "resident-handle",
        session_key: "agent:pore",
      }),
    ).toBe("@pore");
  });
});

describe("deriveWsUrl profile client id", () => {
  it("appends the persisted client id alongside the token", () => {
    const url = deriveWsUrl("/ws", "tok-1", null, "anon-abc");
    expect(url).toContain("token=tok-1");
    expect(url).toContain("client_id=anon-abc");
  });

  it("omits client_id when the profile has none yet", () => {
    const url = deriveWsUrl("/ws", "tok-1", null, null);
    expect(url).not.toContain("client_id");
  });
});

describe("IdentitySettings", () => {
  it("loads and shows the persisted display name", async () => {
    saveProfileClientId("anon-evans");
    mockedFetchIdentity.mockResolvedValue({ client_id: "anon-evans", display_name: "Evans" });
    render(<IdentitySettings />);
    await waitFor(() => {
      expect(screen.getByLabelText("Display name")).toHaveValue("Evans");
    });
    expect(mockedFetchIdentity).toHaveBeenCalledWith(
      expect.anything(),
      "anon-evans",
    );
  });

  it("saves a new name and reports the saved state", async () => {
    saveProfileClientId("anon-evans");
    mockedFetchIdentity.mockResolvedValue({ client_id: "anon-evans", display_name: "" });
    mockedUpdateIdentity.mockResolvedValue({ client_id: "anon-evans", display_name: "Evans" });
    render(<IdentitySettings />);
    await screen.findByLabelText("Display name");
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Evans" } });
    fireEvent.click(screen.getByTestId("identity-save"));
    await waitFor(() => {
      expect(mockedUpdateIdentity).toHaveBeenCalledWith(expect.anything(), "anon-evans", "Evans");
    });
    await screen.findByText(/Identity saved/);
  });

  it("clearing returns to explicit anonymous", async () => {
    saveProfileClientId("anon-evans");
    mockedFetchIdentity.mockResolvedValue({ client_id: "anon-evans", display_name: "Evans" });
    mockedUpdateIdentity.mockResolvedValue({ client_id: "anon-evans", display_name: "" });
    render(<IdentitySettings />);
    await screen.findByTestId("identity-clear");
    fireEvent.click(screen.getByTestId("identity-clear"));
    await waitFor(() => {
      expect(mockedUpdateIdentity).toHaveBeenCalledWith(expect.anything(), "anon-evans", "");
    });
    await screen.findByText(/anonymous again/);
  });

  it("explains attribution-only semantics in the copy", async () => {
    saveProfileClientId("anon-evans");
    mockedFetchIdentity.mockResolvedValue({ client_id: "anon-evans", display_name: "" });
    render(<IdentitySettings />);
    await screen.findByLabelText("Display name");
    expect(screen.getByTestId("identity-settings").textContent).toMatch(/Attribution only/);
    expect(screen.getByTestId("identity-settings").textContent).toMatch(/never rewritten/);
  });
});

describe("MessageBubble speaker label", () => {
  it("shows the verified name for a named human originator", () => {
    const { container } = render(
      <MessageBubble
        message={userMessage({
          originator: { kind: "human", label: "Evans", attestation: "operator-profile" },
        })}
      />,
    );
    expect(container.querySelector("[data-user-originator]")?.textContent).toBe("Evans");
  });

  it("shows Anonymous for an explicitly anonymous originator", () => {
    const { container } = render(
      <MessageBubble message={userMessage({ originator: { kind: "anonymous" } })} />,
    );
    expect(container.querySelector("[data-user-originator]")?.textContent).toBe("Anonymous");
  });

  it("shows the resident handle from a hydrated resident originator", () => {
    const { container } = render(
      <MessageBubble
        message={userMessage({
          originator: {
            kind: "resident",
            handle: "pore",
            attestation: "resident-handle",
            session_key: "agent:pore",
          },
        })}
      />,
    );
    expect(container.querySelector("[data-user-originator]")?.textContent).toBe("@pore");
  });

  it("shows no label for history that predates attribution", () => {
    const { container } = render(<MessageBubble message={userMessage()} />);
    expect(container.querySelector("[data-user-originator]")).toBeNull();
  });
});
