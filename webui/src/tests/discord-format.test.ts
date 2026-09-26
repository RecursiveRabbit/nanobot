import { describe, expect, it } from "vitest";

import { parseDiscordMessage } from "@/lib/discord-format";

const SAMPLE =
  "[discord · Evans Server > #general · Rabbit (@bryn_evans, 435948981948776449) · 2026-09-25 03:58:44 · msg 1552892086439911587]\n" +
  "[mentions: Sexton (1549622206445658122)]\n" +
  "<@1549622206445658122> Are we there?";

describe("parseDiscordMessage", () => {
  it("parses header, mentions trailer, and rewrites mention tokens", () => {
    const parts = parseDiscordMessage(SAMPLE);
    expect(parts).not.toBeNull();
    expect(parts!.guild).toBe("Evans Server");
    expect(parts!.channel).toBe("#general");
    expect(parts!.authorName).toBe("Rabbit");
    expect(parts!.timestamp).toBe("2026-09-25 03:58:44");
    expect(parts!.body).toBe("@Sexton Are we there?");
  });

  it("returns null for ordinary messages", () => {
    expect(parseDiscordMessage("just a normal message")).toBeNull();
  });

  it("keeps unresolved mention ids readable", () => {
    const text = SAMPLE.replace("[mentions: Sexton (1549622206445658122)]\n", "");
    const parts = parseDiscordMessage(text);
    expect(parts!.body).toBe("@1549622206445658122 Are we there?");
  });
});
