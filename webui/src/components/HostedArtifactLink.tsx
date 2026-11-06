import { useTranslation } from "react-i18next";
import { Download } from "lucide-react";

import { FileReferenceIcon, fileKindForPath } from "@/components/FileReferenceChip";
import type { HostedArtifact } from "@/lib/hosted-artifact";
import { cn } from "@/lib/utils";

interface HostedArtifactLinkProps {
  artifact: HostedArtifact;
  /** Author-supplied Markdown label; falls back to the file name. */
  label?: string;
  className?: string;
}

function cleanLabel(value: string | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

/**
 * External open/download route for a hosted artifact (GitHub/GitLab/Bitbucket
 * file URL). Repo and ref/path stay visible as text (no hover required, so it
 * works on mobile), the primary anchor opens the browser URL externally, and a
 * second anchor opens the provider raw-content URL for download. The built-in
 * Markdown reader is intentionally not the only route here.
 */
export function HostedArtifactLink({
  artifact,
  label,
  className,
}: HostedArtifactLinkProps) {
  const { t } = useTranslation();
  const cleaned = cleanLabel(label);
  // A label that is just the URL (or a prefix of it) adds nothing: the
  // structured filename + repo/ref/path context below is clearer.
  const primaryLabel = !cleaned
    || artifact.browserUrl.startsWith(cleaned)
    || artifact.fileName === cleaned
    ? artifact.fileName
    : cleaned;
  const contextText = `${artifact.repo} · ${artifact.refAndPath}`;
  const providerLabel = artifact.provider;

  return (
    <span
      data-testid="hosted-artifact"
      data-provider={artifact.provider}
      className={cn(
        "not-prose my-1 flex w-fit max-w-full items-stretch gap-1.5",
        "rounded-lg border border-border/60 bg-muted/25 px-2.5 py-2",
        className,
      )}
    >
      <a
        href={artifact.browserUrl}
        target="_blank"
        rel="noreferrer noopener"
        data-testid="hosted-artifact-open"
        aria-label={t("message.hostedArtifactOpen", {
          fileName: artifact.fileName,
          provider: providerLabel,
          defaultValue: "Open {{fileName}} on {{provider}}",
        })}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-2",
          "rounded-md text-foreground no-underline",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        )}
      >
        <span className="grid h-7 w-7 shrink-0 place-items-center self-center rounded-md border border-border/55 bg-background text-sky-600 dark:text-sky-300">
          <FileReferenceIcon kind={fileKindForPath(artifact.fileName)} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[13px] font-medium leading-5">
            {primaryLabel}
          </span>
          <span
            data-testid="hosted-artifact-context"
            title={contextText}
            className="block truncate font-mono text-[11px] leading-4 text-muted-foreground"
          >
            {contextText}
          </span>
        </span>
      </a>
      <a
        href={artifact.rawUrl}
        target="_blank"
        rel="noreferrer noopener"
        data-testid="hosted-artifact-download"
        aria-label={t("message.hostedArtifactDownload", {
          fileName: artifact.fileName,
          defaultValue: "Download raw {{fileName}}",
        })}
        className={cn(
          "grid w-8 shrink-0 place-items-center self-center rounded-md border border-border/55",
          "bg-background text-muted-foreground transition-colors",
          "hover:bg-muted hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        )}
      >
        <Download className="h-3.5 w-3.5" aria-hidden />
      </a>
    </span>
  );
}
