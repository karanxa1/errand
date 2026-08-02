// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentBubble } from "./bodies";
import { closeOpenMarkdown } from "./Markdown";

const COMPLETE = `Here is the **plan**, grounded in policy.

| Item | Qty | Price |
| --- | --: | --: |
| Oat milk | 2 | $7.00 |

- pick the cheapest approved merchant
- stop before paying

See [the receipt](https://example.com/receipt) for the trail.

\`\`\`json
{ "budget_cents": 7100 }
\`\`\`
`;

// What the live bubble actually holds a few tokens into a fenced block: the
// opening fence is written, the closing one is not.
const MID_STREAM_UNCLOSED_FENCE = `Building the cart now.

\`\`\`json
{ "budget_cents": 71`;

describe("AgentBubble markdown", () => {
  it("renders a complete markdown reply as real elements", () => {
    const { container } = render(<AgentBubble text={COMPLETE} />);

    expect(screen.getByText("plan").tagName).toBe("STRONG");

    // Table, in its own horizontally scrollable wrapper.
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    expect(screen.getByText("Oat milk").tagName).toBe("TD");
    expect(table?.parentElement?.className).toContain("overflow-x-auto");

    // List.
    expect(container.querySelectorAll("ul li").length).toBe(2);
    expect(screen.getByText("stop before paying").tagName).toBe("LI");

    // Link, opened safely.
    const link = screen.getByText("the receipt") as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("https://example.com/receipt");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer nofollow");

    // Code block, in its own horizontally scrollable wrapper.
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('"budget_cents": 7100');
    expect(pre?.parentElement?.className).toContain("overflow-x-auto");
  });

  it("keeps a mid-stream unclosed ``` fence from swallowing the message", () => {
    const { container } = render(<AgentBubble text={MID_STREAM_UNCLOSED_FENCE} />);

    // The prose before the fence still renders as prose, not as code.
    const lead = screen.getByText("Building the cart now.");
    expect(lead.tagName).toBe("P");
    expect(lead.closest("pre")).toBeNull();

    // And the partial fence body renders as a code block rather than raw
    // backticks bleeding into the paragraph.
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('"budget_cents": 71');
    expect(container.textContent).not.toContain("```");
  });

  it("does not execute raw HTML in model output", () => {
    const { container } = render(
      <AgentBubble text={'<img src=x onerror="alert(1)"> plain <b>text</b>'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("renders every prefix of a streaming message without throwing", () => {
    // The real failure mode is per-token, so walk the whole string.
    for (let i = 1; i <= COMPLETE.length; i += 7) {
      expect(() => render(<AgentBubble text={COMPLETE.slice(0, i)} />)).not.toThrow();
    }
  });
});

describe("closeOpenMarkdown", () => {
  it("leaves balanced text untouched", () => {
    expect(closeOpenMarkdown("a `b` c\n\n```\nx\n```")).toBe("a `b` c\n\n```\nx\n```");
  });

  it("closes an open fence with a matching marker", () => {
    expect(closeOpenMarkdown("```ts\nconst a = 1")).toBe("```ts\nconst a = 1\n```");
    expect(closeOpenMarkdown("~~~\nx\n")).toBe("~~~\nx\n~~~");
    // A longer opener needs an equally long closer.
    expect(closeOpenMarkdown("````\na")).toBe("````\na\n````");
  });

  it("closes a dangling inline span on the line being typed", () => {
    expect(closeOpenMarkdown("run `bun ad")).toBe("run `bun ad`");
  });

  it("does not close a stray backtick from an earlier line", () => {
    // Balancing globally here would swallow two paragraphs into one code span.
    const text = "a stray ` tick\n\nmuch later prose";
    expect(closeOpenMarkdown(text)).toBe(text);
  });

  it("ignores inline backticks while a fence is open", () => {
    expect(closeOpenMarkdown("```\nlet s = `t")).toBe("```\nlet s = `t\n```");
  });
});
