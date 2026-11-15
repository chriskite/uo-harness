import type { ReactNode } from "react";
import { parseMarkdown, type Block, type Inline } from "../markdown.ts";
import { discordSource, splitCodexRefs, type NystulSource } from "../nystul.ts";

/** Opens a cited source; undefined = citations stay plain text and links plain links. */
type OnSource = ((s: NystulSource) => void) | undefined;

/** A link that opens a cited source in place on a plain click; a modified click (new tab,
 *  window) still follows the href. */
export function SourceLink({ href, source, onSource, children }: { href: string; source: NystulSource; onSource: (s: NystulSource) => void; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="ny-ref"
      onClick={(e) => {
        if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        onSource(source);
      }}
    >
      {children}
    </a>
  );
}

function text(v: string, key: number, onSource: OnSource): ReactNode {
  if (!onSource) return v;
  const parts = splitCodexRefs(v);
  if (parts.length === 1 && typeof parts[0] === "string") return v;
  return parts.map((p, i) =>
    typeof p === "string" ? (
      p
    ) : (
      <button key={`${key}.${i}`} type="button" className="ny-ref" title={`Codex entry #${p.id}`} onClick={() => onSource({ kind: "knowledge", id: p.id })}>
        {p.text}
      </button>
    ),
  );
}

function inlines(nodes: Inline[], onSource: OnSource): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text":
        return text(n.v, i, onSource);
      case "br":
        return <br key={i} />;
      case "code":
        return (
          <code key={i} className="mono">
            {n.v}
          </code>
        );
      case "strong":
        return <strong key={i}>{inlines(n.c, onSource)}</strong>;
      case "em":
        return <em key={i}>{inlines(n.c, onSource)}</em>;
      case "link": {
        // a link's label stays plain: no citation buttons inside an anchor
        const src = onSource ? discordSource(n.href) : null;
        return src && onSource ? (
          <SourceLink key={i} href={n.href} source={src} onSource={onSource}>
            {inlines(n.c, undefined)}
          </SourceLink>
        ) : (
          <a key={i} href={n.href} target="_blank" rel="noreferrer">
            {inlines(n.c, undefined)}
          </a>
        );
      }
    }
  });
}

function block(b: Block, key: number, onSource: OnSource): ReactNode {
  const il = (c: Inline[]) => inlines(c, onSource);
  switch (b.t) {
    case "p":
      return <p key={key}>{il(b.c)}</p>;
    case "h":
      return b.level === 1 ? <h3 key={key}>{il(b.c)}</h3> : b.level === 2 ? <h4 key={key}>{il(b.c)}</h4> : <h5 key={key}>{il(b.c)}</h5>;
    case "ul":
    case "ol": {
      const items = b.items.map((it, i) => <li key={i}>{il(it)}</li>);
      return b.t === "ul" ? <ul key={key}>{items}</ul> : <ol key={key}>{items}</ol>;
    }
    case "code":
      return (
        <pre key={key} className="mono" data-lang={b.lang || undefined}>
          <code>{b.text}</code>
        </pre>
      );
    case "quote":
      return <blockquote key={key}>{b.blocks.map((x, i) => block(x, i, onSource))}</blockquote>;
    case "hr":
      return <hr key={key} />;
    case "table":
      return (
        <div key={key} className="md-table">
          <table>
            <thead>
              <tr>
                {b.head.map((c, i) => (
                  <th key={i}>{il(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td key={j}>{il(c)}</td>
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
 *  raw HTML in the text shows as text. Headings render one size below the panel's h2.
 *  With `onSource`, Codex citations (`#123`) and Discord message links open the cited source. */
export function Markdown({ text, onSource }: { text: string; onSource?: (s: NystulSource) => void }) {
  return <div className="md">{parseMarkdown(text).map((b, i) => block(b, i, onSource))}</div>;
}
