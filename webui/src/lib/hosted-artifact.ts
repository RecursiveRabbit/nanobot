/**
 * Hosted source-artifact references (GitHub/GitLab/Bitbucket file URLs).
 *
 * Evans (Townline request 2026-11-05): a hosted artifact reference in a
 * response must be openable/downloadable externally, with repo context
 * visible before clicking. Raw (download) URLs are derived with the exact
 * same ref+path remainder as the browser URL, so branch names containing
 * slashes resolve identically on the provider's side. When the remainder
 * cannot be split into ref/path truthfully (slashed branches), the caller
 * must display the full remainder as text rather than inventing a split.
 */

export type HostedArtifactProvider = "github" | "gitlab" | "bitbucket";

export interface HostedArtifact {
  provider: HostedArtifactProvider;
  /** e.g. "RecursiveRabbit/neoclaw-rails" (GitLab: full group path incl. subgroups). */
  repo: string;
  /**
   * Everything after the blob/src marker: "<ref>/<path>" with the branch and
   * path NOT split, because the split is ambiguous for slashed branches.
   */
  refAndPath: string;
  /** Last path segment of the artifact file. */
  fileName: string;
  /** Absolute browser (blob) URL, normalized to https with query/hash stripped. */
  browserUrl: string;
  /** Absolute raw-content URL for external download/open, same remainder. */
  rawUrl: string;
  /**
   * Display-only split, present ONLY when the remainder has exactly one
   * slash (unambiguous "<ref>/<file>"). Never guessed for slashed branches.
   */
  refLabel?: string;
  pathLabel?: string;
}

const GITHUB_HOST = /^(?:www\.)?github\.com$/i;
const GITLAB_HOST = /^(?:www\.)?gitlab\.com$/i;
const BITBUCKET_HOST = /^(?:www\.)?bitbucket\.org$/i;

function cleanRemainder(value: string): string {
  return value
    .split("/")
    .filter((segment) => segment && segment !== "." && segment !== "..")
    .join("/");
}

function fileNameOf(refAndPath: string): string {
  const segments = refAndPath.split("/");
  return segments[segments.length - 1] ?? refAndPath;
}

function buildArtifact(
  provider: HostedArtifactProvider,
  host: string,
  repo: string,
  rawBase: (repo: string) => string,
  remainder: string,
): HostedArtifact | null {
  const refAndPath = cleanRemainder(remainder);
  // A blob/src URL without a path segment is not a file reference.
  if (!refAndPath.includes("/")) return null;
  const fileName = fileNameOf(refAndPath);
  if (!fileName) return null;

  const browserByProvider: Record<HostedArtifactProvider, string> = {
    github: `https://${host}/${repo}/blob/${refAndPath}`,
    gitlab: `https://${host}/${repo}/-/blob/${refAndPath}`,
    bitbucket: `https://${host}/${repo}/src/${refAndPath}`,
  };

  const artifact: HostedArtifact = {
    provider,
    repo,
    refAndPath,
    fileName,
    browserUrl: browserByProvider[provider],
    rawUrl: `${rawBase(repo)}${refAndPath}`,
  };

  const splitAt = refAndPath.indexOf("/");
  if (splitAt > 0 && !refAndPath.slice(splitAt + 1).includes("/")) {
    artifact.refLabel = refAndPath.slice(0, splitAt);
    artifact.pathLabel = refAndPath.slice(splitAt + 1);
  }
  return artifact;
}

/**
 * Parse a hosted file (blob/src) URL into an artifact reference.
 * Returns null for anything else — callers must not invent targets.
 */
export function hostedArtifactFromUrl(href: string): HostedArtifact | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const host = url.hostname.toLowerCase();
  // Normalize to the canonical https host; strip query and hash entirely.
  const path = url.pathname.replace(/\/+$/, "");

  if (GITHUB_HOST.test(host)) {
    const match = /^\/([^/]+)\/([^/]+)\/blob\/(.+)$/.exec(path);
    if (!match) return null;
    const [, org, name, remainder] = match;
    if (!org || !name || org === "-" || name === "-") return null;
    return buildArtifact(
      "github",
      "github.com",
      `${org}/${name}`,
      (repo) => `https://raw.githubusercontent.com/${repo}/`,
      remainder,
    );
  }

  if (GITLAB_HOST.test(host)) {
    const match = /^\/(.+)\/-\/blob\/(.+)$/.exec(path);
    if (!match) return null;
    const [, repo, remainder] = match;
    if (!repo) return null;
    return buildArtifact(
      "gitlab",
      "gitlab.com",
      repo,
      (repoPath) => `https://gitlab.com/${repoPath}/-/raw/`,
      remainder,
    );
  }

  if (BITBUCKET_HOST.test(host)) {
    const match = /^\/([^/]+)\/([^/]+)\/src\/(.+)$/.exec(path);
    if (!match) return null;
    const [, workspace, name, remainder] = match;
    if (!workspace || !name) return null;
    return buildArtifact(
      "bitbucket",
      "bitbucket.org",
      `${workspace}/${name}`,
      (repo) => `https://bitbucket.org/${repo}/raw/`,
      remainder,
    );
  }

  return null;
}
