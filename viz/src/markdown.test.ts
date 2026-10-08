import { describe, expect, test } from "bun:test";
import { parseInline, parseMarkdown } from "./markdown.ts";

describe("parseMarkdown blocks", () => {
  test("headings, paragraphs with kept line breaks, rules", () => {
    expect(parseMarkdown("# Title\n### Small\nline one\nline two\n\n---\nafter")).toEqual([
      { t: "h", level: 1, c: [{ t: "text", v: "Title" }] },
      { t: "h", level: 3, c: [{ t: "text", v: "Small" }] },
      { t: "p", c: [{ t: "text", v: "line one" }, { t: "br" }, { t: "text", v: "line two" }] },
      { t: "hr" },
      { t: "p", c: [{ t: "text", v: "after" }] },
    ]);
  });

  test("bullet and numbered lists, continuation lines join the item", () => {
    expect(parseMarkdown("- a\n* b\n  more\n\n1. one\n2. two")).toEqual([
      { t: "ul", items: [[{ t: "text", v: "a" }], [{ t: "text", v: "b more" }]] },
      { t: "ol", items: [[{ t: "text", v: "one" }], [{ t: "text", v: "two" }]] },
    ]);
  });

  test("pipe table with header and separator; short rows pad, long rows trim", () => {
    expect(parseMarkdown("| skill | value |\n|---|:---:|\n| Lumberjacking | **98.4** |\n| Tactics |\n| a | b | c |")).toEqual([
      {
        t: "table",
        head: [[{ t: "text", v: "skill" }], [{ t: "text", v: "value" }]],
        rows: [
          [[{ t: "text", v: "Lumberjacking" }], [{ t: "strong", c: [{ t: "text", v: "98.4" }] }]],
          [[{ t: "text", v: "Tactics" }], []],
          [[{ t: "text", v: "a" }], [{ t: "text", v: "b" }]],
        ],
      },
    ]);
  });

  test("fenced code keeps its language and raw text", () => {
    expect(parseMarkdown("```sql\nSELECT * FROM knowledge\n  WHERE id = 1\n```\nafter")).toEqual([
      { t: "code", lang: "sql", text: "SELECT * FROM knowledge\n  WHERE id = 1" },
      { t: "p", c: [{ t: "text", v: "after" }] },
    ]);
  });

  test("an unterminated fence becomes a code block to the end", () => {
    expect(parseMarkdown("intro\n```\ncode **not bold**\n\n# not a heading")).toEqual([
      { t: "p", c: [{ t: "text", v: "intro" }] },
      { t: "code", lang: "", text: "code **not bold**\n\n# not a heading" },
    ]);
  });

  test("quotes hold nested blocks", () => {
    expect(parseMarkdown("> the Codex says\n> - one")).toEqual([
      {
        t: "quote",
        blocks: [
          { t: "p", c: [{ t: "text", v: "the Codex says" }] },
          { t: "ul", items: [[{ t: "text", v: "one" }]] },
        ],
      },
    ]);
  });

  test("CRLF input parses like LF", () => {
    expect(parseMarkdown("a\r\nb")).toEqual(parseMarkdown("a\nb"));
  });
});

describe("parseInline", () => {
  test("code, strong, em in both spellings", () => {
    expect(parseInline("`x*y` **b** *i* _u_")).toEqual([
      { t: "code", v: "x*y" },
      { t: "text", v: " " },
      { t: "strong", c: [{ t: "text", v: "b" }] },
      { t: "text", v: " " },
      { t: "em", c: [{ t: "text", v: "i" }] },
      { t: "text", v: " " },
      { t: "em", c: [{ t: "text", v: "u" }] },
    ]);
  });

  test("**a *b*** nests em inside strong", () => {
    expect(parseInline("**a *b***")).toEqual([
      { t: "strong", c: [{ t: "text", v: "a " }, { t: "em", c: [{ t: "text", v: "b" }] }] },
    ]);
  });

  test("snake_case words and lone stars stay text", () => {
    expect(parseInline("harvest_nodes and 2 * 3")).toEqual([{ t: "text", v: "harvest_nodes and 2 * 3" }]);
  });

  test("http(s) and # links are links", () => {
    expect(parseInline("[docs](https://example.com/a) [jobs](#jobs)")).toEqual([
      { t: "link", href: "https://example.com/a", c: [{ t: "text", v: "docs" }] },
      { t: "text", v: " " },
      { t: "link", href: "#jobs", c: [{ t: "text", v: "jobs" }] },
    ]);
  });

  test("[x](javascript:alert(1)) stays text, not a link", () => {
    const out = parseInline("[x](javascript:alert(1))");
    expect(out).toEqual([{ t: "text", v: "[x](javascript:alert(1))" }]);
    expect(parseInline("[x](data:text/html,hi) [y](docs/NOTES.md)").some((n) => n.t === "link")).toBe(false);
  });

  test("raw HTML stays a text node", () => {
    expect(parseMarkdown('<img src=x onerror="alert(1)">')).toEqual([
      { t: "p", c: [{ t: "text", v: '<img src=x onerror="alert(1)">' }] },
    ]);
  });
});
