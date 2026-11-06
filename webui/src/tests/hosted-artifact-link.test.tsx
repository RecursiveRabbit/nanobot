import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FileReferenceChip } from "@/components/FileReferenceChip";
import MarkdownTextRenderer from "@/components/MarkdownTextRenderer";

const FIXTURE_BROWSER =
  "https://github.com/RecursiveRabbit/neoclaw-rails/blob/docs/babo-neoclaw-integration/docs/BABO-NEOCLAW-INTEGRATION.md";
const FIXTURE_RAW =
  "https://raw.githubusercontent.com/RecursiveRabbit/neoclaw-rails/docs/babo-neoclaw-integration/docs/BABO-NEOCLAW-INTEGRATION.md";

describe("hosted artifact links", () => {
  it("renders the acceptance fixture with external open, download, and repo context", () => {
    render(
      <MarkdownTextRenderer>
        {`See [docs/BABO-NEOCLAW-INTEGRATION.md](${FIXTURE_BROWSER}) for the integration notes.`}
      </MarkdownTextRenderer>,
    );

    const card = screen.getByTestId("hosted-artifact");
    expect(card).toHaveAttribute("data-provider", "github");

    // External open route: real anchor, absolute URL, new tab (mobile-tappable).
    const open = screen.getByTestId("hosted-artifact-open");
    expect(open).toHaveAttribute("href", FIXTURE_BROWSER);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", expect.stringContaining("noopener"));

    // Repo context is visible text, not a hover tooltip.
    const context = screen.getByTestId("hosted-artifact-context");
    expect(context).toHaveTextContent("RecursiveRabbit/neoclaw-rails");
    expect(context).toHaveTextContent("docs/babo-neoclaw-integration");
    expect(context).toHaveTextContent("docs/BABO-NEOCLAW-INTEGRATION.md");

    // External download route: provider raw URL, same slashed branch.
    const download = screen.getByTestId("hosted-artifact-download");
    expect(download).toHaveAttribute("href", FIXTURE_RAW);
    expect(download).toHaveAttribute("target", "_blank");
  });

  it("shows the file name as the primary label for full-URL link text", () => {
    render(
      <MarkdownTextRenderer>
        {`Read ${FIXTURE_BROWSER} first.`}
      </MarkdownTextRenderer>,
    );

    expect(screen.getByTestId("hosted-artifact-open")).toHaveTextContent(
      "BABO-NEOCLAW-INTEGRATION.md",
    );
  });

  it("keeps a descriptive author label as the primary text", () => {
    render(
      <MarkdownTextRenderer>
        {`Read [the integration writeup](${FIXTURE_BROWSER}) first.`}
      </MarkdownTextRenderer>,
    );

    expect(screen.getByTestId("hosted-artifact-open")).toHaveTextContent(
      "the integration writeup",
    );
    expect(screen.getByTestId("hosted-artifact-context")).toHaveTextContent(
      "RecursiveRabbit/neoclaw-rails",
    );
  });

  it("leaves ordinary external links as plain anchors", () => {
    render(
      <MarkdownTextRenderer>
        {"Visit [the docs](https://docs.example.com/start) now."}
      </MarkdownTextRenderer>,
    );

    expect(screen.queryByTestId("hosted-artifact")).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "the docs" });
    expect(link).toHaveAttribute("href", "https://docs.example.com/start");
    expect(link).toHaveClass("text-blue-500");
  });
});

describe("FileReferenceChip without a target", () => {
  it("does not look like a link when it has no target", () => {
    render(<FileReferenceChip path="BABO-NEOCLAW-INTEGRATION.md" />);

    const chip = screen.getByTestId("inline-file-path");
    expect(chip).not.toHaveAttribute("role");
    expect(chip).not.toHaveAttribute("tabindex");
    expect(chip).not.toHaveClass("text-sky-600", "dark:text-sky-300");
    expect(chip.className).toContain("bg-muted");
    expect(chip.className).toContain("font-mono");
  });

  it("keeps link styling when an open target exists", () => {
    render(
      <FileReferenceChip
        path="agent/hook.py"
        onOpen={() => {}}
      />,
    );

    const chip = screen.getByTestId("inline-file-path");
    expect(chip).toHaveAttribute("role", "button");
    expect(chip).toHaveClass("text-sky-600", "dark:text-sky-300");
  });
});
