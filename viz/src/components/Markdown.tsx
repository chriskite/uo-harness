import type { ReactNode } from "react";
import { parseMarkdown, type Block, type Inline } from "../markdown.ts";

function inlines(nodes: Inline[]): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text":
        return n.v;
      case "br":
        return <br key={i} />;
      case "code":
        return (
          <code key={i} className="mono">
            {n.v}
          </code>
        );
      case "strong":
        return <strong key={i}>{inlines(n.c)}</strong>;
      case "em":
        return <em key={i}>{inlines(n.c)}</em>;
      case "link":
        return (
          <a key={i} href={n.href} target="_blank" rel="noreferrer">
            {inlines(n.c)}
          </a>
        );
    }
  });
}

function block(b: Block, key: number): ReactNode {
  switch (b.t) {
    case "p":
      return <p key={key}>{inlines(b.c)}</p>;
    case "h":
      return b.level === 1 ? <h3 key={key}>{inlines(b.c)}</h3> : b.level === 2 ? <h4 key={key}>{inlines(b.c)}</h4> : <h5 key={key}>{inlines(b.c)}</h5>;
    case "ul":
    case "ol": {
      const items = b.items.map((it, i) => <li key={i}>{inlines(it)}</li>);
      return b.t === "ul" ? <ul key={key}>{items}</ul> : <ol key={key}>{items}</ol>;
    }
    case "code":
      return (
        <pre key={key} className="mono" data-lang={b.lang || undefined}>
          <code>{b.text}</code>
        </pre>
      );
    case "quote":
      return <blockquote key={key}>{b.blocks.map(block)}</blockquote>;
    case "hr":
      return <hr key={key} />;
    case "table":
      return (
        <div key={key} className="md-table">
          <table>
            <thead>
              <tr>
                {b.head.map((c, i) => (
                  <th key={i}>{inlines(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td key={j}>{inlines(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

/** Markdown (markdown.ts subset) as React elements: React escapes every string, so
 *  raw HTML in the text shows as text. Headings render one size below the panel's h2. */
export function Markdown({ text }: { text: string }) {
  return <div className="md">{parseMarkdown(text).map(block)}</div>;
}
