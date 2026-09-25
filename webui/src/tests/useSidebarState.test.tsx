import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useSidebarState } from "@/hooks/useSidebarState";
import type { NanobotClient } from "@/lib/nanobot-client";
import type { SidebarStatePayload } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";

describe("useSidebarState", () => {
  it("serializes full-state writes so an older request cannot overwrite a newer update", async () => {
    let resolveFirstWrite: (() => void) | null = null;
    let sidebarStateUpdateHandler: ((state: SidebarStatePayload) => void) | null = null;
    const setSidebarState = vi.fn()
      .mockImplementationOnce((state: SidebarStatePayload) => new Promise<SidebarStatePayload>(
        (resolve) => {
          resolveFirstWrite = () => resolve(state);
        },
      ))
      .mockImplementation(async (state: SidebarStatePayload) => state);
    const client = {
      status: "open" as const,
      onStatus: () => () => {},
      onSidebarStateUpdate: (handler: (state: SidebarStatePayload) => void) => {
        sidebarStateUpdateHandler = handler;
        return () => {
          sidebarStateUpdateHandler = null;
        };
      },
      setSidebarState,
    } as unknown as NanobotClient;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    }));
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ClientProvider client={client} token="token">
        {children}
      </ClientProvider>
    );
    const { result } = renderHook(() => useSidebarState(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      void result.current.update((current) => ({
        ...current,
        title_overrides: { "websocket:a": "First" },
      }));
      void result.current.update((current) => ({
        ...current,
        title_overrides: { "websocket:a": "Second" },
      }));
    });

    expect(setSidebarState).toHaveBeenCalledTimes(1);
    act(() => {
      sidebarStateUpdateHandler?.(setSidebarState.mock.calls[0]?.[0]);
    });
    expect(result.current.state.title_overrides).toEqual({
      "websocket:a": "Second",
    });
    act(() => resolveFirstWrite?.());
    await waitFor(() => expect(setSidebarState).toHaveBeenCalledTimes(2));
    expect(setSidebarState.mock.calls[1]?.[0]).toEqual(expect.objectContaining({
      title_overrides: { "websocket:a": "Second" },
    }));
  });
});

describe("sidebar persistence regression", () => {
  it("never prunes overrides for sessions missing from the list", async () => {
    // Evans' renames/groups kept vanishing on refresh: the hook auto-persisted
    // a state pruned of any session absent from the current list. The write
    // path is fine; the prune write-back was the wipe.
    const setSidebarState = vi.fn(async (state: SidebarStatePayload) => state);
    const client = {
      status: "open" as const,
      onStatus: () => () => {},
      onSidebarStateUpdate: () => () => {},
      setSidebarState,
    } as unknown as NanobotClient;
    const loadedState: SidebarStatePayload = {
      schema_version: 1,
      pinned_keys: [],
      archived_keys: [],
      session_order: [],
      title_overrides: { "websocket:missing-from-list": "Sexton" },
      project_name_overrides: { sysadmin: "Sysadmin" },
      tags_by_key: { "websocket:missing-from-list": ["sysadmin"] },
      collapsed_groups: {},
      workbench: { version: 1, tabs: {} },
      view: {
        density: "comfortable",
        show_previews: false,
        show_timestamps: false,
        show_archived: false,
        sort: "updated_desc",
      },
      updated_at: "2026-09-24T00:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => loadedState,
    }));
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ClientProvider client={client} token="token">
        {children}
      </ClientProvider>
    );

    const { result } = renderHook(() => useSidebarState(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    // The override survives in state...
    expect(result.current.state.title_overrides["websocket:missing-from-list"]).toBe("Sexton");
    // ...and nothing is written back merely because the session list lacks it.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(setSidebarState).not.toHaveBeenCalled();
  });
});
