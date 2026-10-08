// A small markdown subset for Nystul's answers (components/Markdown.tsx renders it
// with React elements only, so nothing here is ever HTML: raw tags stay text).
// Blocks: paragraphs (single newlines kept as line breaks), # to ### headings,
// one-level - / * / 1. lists, fenced code, > quotes, --- rules and pipe tables.
// Inline: `code`, **strong**, *em* / _em_, [text](href) with http(s) or # hrefs only.

export type Inline =
  | { t: "text"; v: string }
  | { t: "br" }
  | { t: "code"; v: string }
  | { t: "strong" | "em"; c: Inline[] }
  | { t: "link"; href: string; c: Inline[] };

export type Block =
  | { t: "p"; c: Inline[] }
  | { t: "h"; level: 1 | 2 | 3; c: Inline[] }
  | { t: "ul" | "ol"; items: Inline[][] }
  | { t: "code"; lang: string; text: string }
  | { t: "quote"; blocks: Block[] }
  | { t: "hr" }
  | { t: "table"; head: Inline[][]; rows: Inline[][][] };

const SAFE_HREF = /^(https?:\/\/|#)/i;

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*)/;
const HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)(\s+#+)?\s*$/;
const QUOTE = /^\s{0,3}>/;
const UL_ITEM = /^\s*[-*+]\s+(.*)$/;
const OL_ITEM = /^\s*\d{1,9}[.)]\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

const isBlank = (l: string) => l.trim() === "";

function isTableStart(lines: string[], i: number): boolean {
  const head = lines[i];
  const sep = lines[i + 1];
  return head !== undefined && sep !== undefined && head.includes("|") && sep.includes("|") && TABLE_SEP.test(sep);
}

/** Does line i open a block other than a paragraph? */
function startsBlock(lines: string[], i: number): boolean {
  const l = lines[i] ?? "";
  return FENCE.test(l) || HR.test(l) || HEADING.test(l) || QUOTE.test(l) || UL_ITEM.test(l) || OL_ITEM.test(l) || isTableStart(lines, i);
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

export function parseMarkdown(src: string): Block[] {
  return parseLines(src.replace(/\r\n?/g, "\n").split("\n"));
}

function parseLines(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (isBlank(line)) {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1] ?? "```";
      const body: string[] = [];
      i++;
      // An unterminated fence runs to the end of the text.
      while (i < lines.length) {
        const l = lines[i] ?? "";
        if (l.trim().startsWith(marker) && l.trim().replace(/[`~]/g, "") === "") break;
        body.push(l);
        i++;
      }
      i++; // the closing fence (or past the end)
      blocks.push({ t: "code", lang: fence[2] ?? "", text: body.join("\n") });
      continue;
    }

    if (HR.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = Math.min((heading[1] ?? "#").length, 3) as 1 | 2 | 3;
      blocks.push({ t: "h", level, c: parseInline(heading[2] ?? "") });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i] ?? "")) {
        inner.push((lines[i] ?? "").replace(/^\s{0,3}> ?/, ""));
        i++;
      }
      blocks.push({ t: "quote", blocks: parseLines(inner) });
      continue;
    }

    if (isTableStart(lines, i)) {
      const head = splitRow(line);
      const width = head.length;
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length) {
        const l = lines[i] ?? "";
        if (isBlank(l) || !l.includes("|")) break;
        const cells = splitRow(l).slice(0, width);
        while (cells.length < width) cells.push("");
        rows.push(cells.map(parseInline));
        i++;
      }
      blocks.push({ t: "table", head: head.map(parseInline), rows });
      continue;
    }

    const ordered = OL_ITEM.test(line);
    if (ordered || UL_ITEM.test(line)) {
      const itemRe = ordered ? OL_ITEM : UL_ITEM;
      const otherRe = ordered ? UL_ITEM : OL_ITEM;
      const items: string[] = [];
      while (i < lines.length) {
        const l = lines[i] ?? "";
        const m = itemRe.exec(l);
        if (m) {
          items.push(m[1] ?? "");
          i++;
          continue;
        }
        if (isBlank(l)) {
          // A blank line between items keeps the list going (a loose list).
          let j = i;
          while (j < lines.length && isBlank(lines[j] ?? "")) j++;
          if (j < lines.length && itemRe.test(lines[j] ?? "")) {
            i = j;
            continue;
          }
          break;
        }
        if (otherRe.test(l) || startsBlock(lines, i)) break;
        // A continuation line of the current item.
        items[items.length - 1] += " " + l.trim();
        i++;
      }
      blocks.push({ t: ordered ? "ol" : "ul", items: items.map(parseInline) });
      continue;
    }

    const para: string[] = [line];
    i++;
    while (i < lines.length && !isBlank(lines[i] ?? "") && !startsBlock(lines, i)) {
      para.push(lines[i] ?? "");
      i++;
    }
    blocks.push({ t: "p", c: parseInline(para.map((l) => l.trim()).join("\n")) });
  }
  return blocks;
}

const isSpace = (ch: string | undefined) => ch === undefined || /\s/.test(ch);
const isWord = (ch: string | undefined) => ch !== undefined && /[\p{L}\p{N}]/u.test(ch);

/** Count of `*` in s outside code spans (balanced emphasis has an even count). */
function starCount(s: string): number {
  let n = 0;
  let inCode = false;
  for (const ch of s) {
    if (ch === "`") inCode = !inCode;
    else if (ch === "*" && !inCode) n++;
  }
  return n;
}

/** Closing `**` for a strong opened at `from`, or -1. */
function closeStrong(s: string, from: number): number {
  for (let j = s.indexOf("**", from); j !== -1; j = s.indexOf("**", j + 1)) {
    const inner = s.slice(from, j);
    if (inner.length > 0 && !isSpace(s[j - 1]) && starCount(inner) % 2 === 0) return j;
  }
  return -1;
}

/** Closing single `*` for an em opened at `from` (inner `**` runs are skipped), or -1. */
function closeStarEm(s: string, from: number): number {
  let j = from;
  while (j < s.length) {
    const k = s.indexOf("*", j);
    if (k === -1) return -1;
    if (s[k + 1] === "*") {
      j = k + 2;
      continue;
    }
    const inner = s.slice(from, k);
    if (inner.length > 0 && !isSpace(s[k - 1]) && starCount(inner) % 2 === 0) return k;
    j = k + 1;
  }
  return -1;
}

/** Closing `_` for an em opened at `from` (not inside a word), or -1. */
function closeUnderEm(s: string, from: number): number {
  for (let j = s.indexOf("_", from); j !== -1; j = s.indexOf("_", j + 1)) {
    if (j > from && !isSpace(s[j - 1]) && !isWord(s[j + 1])) return j;
  }
  return -1;
}

/** End index (exclusive) and target of a [text](href) at `i`, or null. */
function linkAt(s: string, i: number): { textEnd: number; href: string; end: number } | null {
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    const ch = s[j];
    if (ch === "\n") return null;
    if (ch === "[") depth++;
    else if (ch === "]" && --depth === 0) {
      if (s[j + 1] !== "(") return null;
      const close = s.indexOf(")", j + 2);
      if (close === -1) return null;
      const href = s.slice(j + 2, close).trim();
      if (href === "" || /\s/.test(href)) return null;
      return { textEnd: j, href, end: close + 1 };
    }
  }
  return null;
}

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ t: "text", v: text });
    text = "";
  };
  const push = (node: Inline) => {
    flush();
    out.push(node);
  };

  let i = 0;
  while (i < s.length) {
    const ch = s[i] ?? "";
    if (ch === "\n") {
      push({ t: "br" });
      i++;
      continue;
    }
    if (ch === "`") {
      const close = s.indexOf("`", i + 1);
      if (close > i + 1) {
        push({ t: "code", v: s.slice(i + 1, close) });
        i = close + 1;
        continue;
      }
    }
    if (ch === "*" && s[i + 1] === "*" && !isSpace(s[i + 2])) {
      const close = closeStrong(s, i + 2);
      if (close !== -1) {
        push({ t: "strong", c: parseInline(s.slice(i + 2, close)) });
        i = close + 2;
        continue;
      }
    }
    if (ch === "*" && s[i + 1] !== "*" && !isSpace(s[i + 1])) {
      const close = closeStarEm(s, i + 1);
      if (close !== -1) {
        push({ t: "em", c: parseInline(s.slice(i + 1, close)) });
        i = close + 1;
        continue;
      }
    }
    if (ch === "_" && !isWord(s[i - 1]) && !isSpace(s[i + 1])) {
      const close = closeUnderEm(s, i + 1);
      if (close !== -1) {
        push({ t: "em", c: parseInline(s.slice(i + 1, close)) });
        i = close + 1;
        continue;
      }
    }
    if (ch === "[") {
      const link = linkAt(s, i);
      // Unsafe targets (javascript:, data:, relative paths) stay literal text.
      if (link && SAFE_HREF.test(link.href)) {
        push({ t: "link", href: link.href, c: parseInline(s.slice(i + 1, link.textEnd)) });
        i = link.end;
        continue;
      }
    }
    text += ch;
    i++;
  }
  flush();
  return out;
}
