import { describe, expect, it } from "vitest";

import { hostedArtifactFromUrl } from "@/lib/hosted-artifact";

const FIXTURE_BROWSER =
  "https://github.com/RecursiveRabbit/neoclaw-rails/blob/docs/babo-neoclaw-integration/docs/BABO-NEOCLAW-INTEGRATION.md";
const FIXTURE_RAW =
  "https://raw.githubusercontent.com/RecursiveRabbit/neoclaw-rails/docs/babo-neoclaw-integration/docs/BABO-NEOCLAW-INTEGRATION.md";

describe("hostedArtifactFromUrl", () => {
  it("parses the acceptance fixture with a slash-containing branch", () => {
    const artifact = hostedArtifactFromUrl(FIXTURE_BROWSER);
    expect(artifact).not.toBeNull();
    expect(artifact?.provider).toBe("github");
    expect(artifact?.repo).toBe("RecursiveRabbit/neoclaw-rails");
    expect(artifact?.refAndPath).toBe(
      "docs/babo-neoclaw-integration/docs/BABO-NEOCLAW-INTEGRATION.md",
    );
    expect(artifact?.fileName).toBe("BABO-NEOCLAW-INTEGRATION.md");
    expect(artifact?.browserUrl).toBe(FIXTURE_BROWSER);
    expect(artifact?.rawUrl).toBe(FIXTURE_RAW);
    // Slashed branches cannot be split truthfully: no invented ref/path.
    expect(artifact?.refLabel).toBeUndefined();
    expect(artifact?.pathLabel).toBeUndefined();
  });

  it("splits ref and path only when the remainder is unambiguous", () => {
    const artifact = hostedArtifactFromUrl(
      "https://github.com/org/repo/blob/main/README.md",
    );
    expect(artifact?.refLabel).toBe("main");
    expect(artifact?.pathLabel).toBe("README.md");
    expect(artifact?.rawUrl).toBe(
      "https://raw.githubusercontent.com/org/repo/main/README.md",
    );
  });

  it("normalizes http and strips query and hash", () => {
    const artifact = hostedArtifactFromUrl(
      "http://github.com/org/repo/blob/main/README.md?plain=1#L3",
    );
    expect(artifact?.browserUrl).toBe(
      "https://github.com/org/repo/blob/main/README.md",
    );
    expect(artifact?.rawUrl).toBe(
      "https://raw.githubusercontent.com/org/repo/main/README.md",
    );
  });

  it("maps GitLab blob URLs including subgroup paths", () => {
    const artifact = hostedArtifactFromUrl(
      "https://gitlab.com/group/subgroup/project/-/blob/feature/x/src/app.ts",
    );
    expect(artifact?.provider).toBe("gitlab");
    expect(artifact?.repo).toBe("group/subgroup/project");
    expect(artifact?.refAndPath).toBe("feature/x/src/app.ts");
    expect(artifact?.rawUrl).toBe(
      "https://gitlab.com/group/subgroup/project/-/raw/feature/x/src/app.ts",
    );
  });

  it("maps Bitbucket src URLs", () => {
    const artifact = hostedArtifactFromUrl(
      "https://bitbucket.org/workspace/repo/src/master/dir/notes.md",
    );
    expect(artifact?.provider).toBe("bitbucket");
    expect(artifact?.repo).toBe("workspace/repo");
    expect(artifact?.rawUrl).toBe(
      "https://bitbucket.org/workspace/repo/raw/master/dir/notes.md",
    );
  });

  it("returns null for non-file GitHub pages", () => {
    expect(
      hostedArtifactFromUrl("https://github.com/org/repo/issues/42"),
    ).toBeNull();
    expect(hostedArtifactFromUrl("https://github.com/org/repo")).toBeNull();
    expect(
      hostedArtifactFromUrl("https://github.com/org/repo/blob/main"),
    ).toBeNull();
    expect(
      hostedArtifactFromUrl("https://github.com/org/repo/pulls"),
    ).toBeNull();
  });

  it("returns null for other hosts and non-http protocols", () => {
    expect(hostedArtifactFromUrl("https://example.com/a/b/blob/x/y.md")).toBeNull();
    expect(hostedArtifactFromUrl("ftp://github.com/org/repo/blob/main/a.md")).toBeNull();
    expect(hostedArtifactFromUrl("not a url")).toBeNull();
  });
});
