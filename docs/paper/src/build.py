"""Build docs/paper/uo-harness-paper.html: one self-contained page, no network, no runtime libraries.

    python docs/paper/src/build.py [--no-charts] [--only 'NN-*' --out PREVIEW.html]
    python docs/paper/src/build.py --pseudonymize   # -> uo-harness-paper-pseudonymized.html

Steps:
  1. run every charts/fig_*.py (each writes figures/<name>.svg from data/*.json)
  2. assemble template.html + style.css + hero.html + sections/*.html (sorted), inlining
     <!--#include path--> directives (paths relative to this directory)
  3. prerender in headless Chromium (Playwright): mermaid <pre class="mermaid"> -> inline SVG
     (fixed rough.js seed, labelled by its figcaption), .math elements (TeX) -> MathML via temml,
     figure SVGs get a scroll wrapper and their natural width (narrow-screen minimum, style.css),
     section/figure/table/equation numbering, <a class="ref" href="#id"></a> cross-references,
     hover tooltips on packet ids from packets.json (<abbr class="pkt" title> in text, an SVG
     <title> on diagram and chart text), the tables of contents
  4. strip the build-time scripts and write the static page

--pseudonymize builds the shareable edition from the same sources with the rules in pseudonyms.json:
the charts are re-rendered into a temp dir with their text rewritten before layout (the committed
figures are not touched; --no-charts does not apply), the assembled page is rewritten (prose,
captions, mermaid sources, byline, plus an edition note), and the finished page is refused if any
of the rules' forbidden patterns is still in it.

Needs: pip install playwright && python -m playwright install chromium; `bun install` in this dir
(mermaid + temml). Fails on a broken diagram, bad TeX, a duplicate id, a dangling reference or a
packet id that packets.json does not describe.
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(os.path.dirname(os.path.abspath(__file__)))
OUT = SRC.parent / "uo-harness-paper.html"
INCLUDE = re.compile(r"<!--#include\s+(\S+?)\s*-->")

PRERENDER_JS = r"""
async (PACKETS) => {
  const errors = [];
  const slug = s => s.toLowerCase().replace(/<[^>]+>/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

  // 1. TeX -> MathML
  const macros = { "\\E": "\\mathbb{E}", "\\R": "\\mathbb{R}", "\\Var": "\\operatorname{Var}" };
  for (const el of document.querySelectorAll('.math')) {
    const tex = el.textContent.trim();
    try {
      el.innerHTML = temml.renderToString(tex, { displayMode: el.tagName === 'DIV', throwOnError: true, macros });
    } catch (e) { errors.push('math: ' + e.message + ' :: ' + tex.slice(0, 100)); }
  }

  // 2. mermaid -> inline SVG
  const sans = '"Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif';
  mermaid.initialize({
    // handDrawnSeed: mermaid draws node/edge shapes with rough.js even in the classic look; seed 0 means
    // Math.random, which made the ER diagram's border paths differ on every build
    startOnLoad: false, theme: 'base', look: 'classic', handDrawnSeed: 1, fontFamily: sans, securityLevel: 'loose',
    themeVariables: {
      fontFamily: sans, fontSize: '14px', textColor: '#1f2328', lineColor: '#6b7480',
      primaryColor: '#eaf1f7', primaryBorderColor: '#1f5f8b', primaryTextColor: '#1f2328',
      secondaryColor: '#fbeee6', secondaryBorderColor: '#b4572b', secondaryTextColor: '#1f2328',
      tertiaryColor: '#f4f1ea', tertiaryBorderColor: '#cfc8b8', tertiaryTextColor: '#1f2328',
      mainBkg: '#eaf1f7', nodeBorder: '#1f5f8b', clusterBkg: '#f7f5f0', clusterBorder: '#cfc8b8',
      titleColor: '#1f2328', edgeLabelBackground: '#ffffff',
      noteBkgColor: '#fbf3dc', noteBorderColor: '#b8892a', noteTextColor: '#1f2328',
      actorBkg: '#eaf1f7', actorBorder: '#1f5f8b', actorTextColor: '#1f2328', actorLineColor: '#9aa7b4',
      signalColor: '#4a5059', signalTextColor: '#1f2328', labelBoxBkgColor: '#f4f1ea',
      labelBoxBorderColor: '#cfc8b8', labelTextColor: '#1f2328', loopTextColor: '#1f2328',
      activationBkgColor: '#fbeee6', activationBorderColor: '#b4572b', sequenceNumberColor: '#ffffff',
      stateBkg: '#eaf1f7', stateBorder: '#1f5f8b', transitionColor: '#6b7480', transitionLabelColor: '#4a5059',
      compositeBackground: '#f7f5f0', altBackground: '#f7f5f0',
    },
    flowchart: { curve: 'basis', padding: 12, nodeSpacing: 36, rankSpacing: 48, htmlLabels: true, useMaxWidth: true },
    sequence: { mirrorActors: false, actorMargin: 40, messageFontSize: 13, noteFontSize: 13, actorFontSize: 13, useMaxWidth: true, wrap: true },
    state: { useMaxWidth: true },
    gantt: { useMaxWidth: true, fontSize: 12, barHeight: 18, barGap: 5, topPadding: 40, leftPadding: 140 },
    timeline: { useMaxWidth: true },
  });
  let k = 0;
  for (const pre of [...document.querySelectorAll('pre.mermaid')]) {
    // raw <br/>, <sub>, <sup> in the HTML source parse as elements; give mermaid their text form back
    pre.querySelectorAll('br').forEach(b => b.replaceWith(document.createTextNode('<br/>')));
    pre.querySelectorAll('sub, sup').forEach(e => e.replaceWith(document.createTextNode(
      '<' + e.localName + '>' + e.textContent + '</' + e.localName + '>')));
    const src = pre.textContent;
    const fig = pre.closest('figure');
    const where = fig?.id || pre.closest('section')?.id || '?';
    const n = k++;
    try {
      const { svg } = await mermaid.render('mmd' + n, src);
      const div = document.createElement('div');
      div.className = 'mermaid-svg';
      div.innerHTML = svg;
      // accessible name: the figure caption (mermaid only sets a role description)
      const cap = fig?.querySelector(':scope > figcaption');
      const el = div.querySelector('svg');
      if (cap && el) {
        if (!cap.id) cap.id = (fig.id || 'mmd' + n) + '-caption';
        el.setAttribute('role', 'img');
        el.setAttribute('aria-labelledby', cap.id);
      } else errors.push('mermaid without figure caption in #' + where);
      pre.replaceWith(div);
    } catch (e) { errors.push('mermaid in #' + where + ': ' + (e.message || e).toString().slice(0, 300)); }
  }
  document.querySelectorAll('body > svg[id^="dmmd"], body > div[id^="dmmd"]').forEach(n => n.remove());

  // 2b. figure SVGs: a bare SVG gets a wrapper that can scroll; every top-level figure SVG records its
  // natural (viewBox) width so narrow screens keep it legible instead of shrinking it (style.css)
  for (const svg of document.querySelectorAll('main figure.fig > svg')) {
    const wrap = document.createElement('div');
    wrap.className = 'fig-scroll';
    svg.replaceWith(wrap);
    wrap.appendChild(svg);
  }
  for (const svg of document.querySelectorAll('main figure.fig svg')) {
    if (svg.parentElement.closest('svg')) continue;
    const w = svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width;
    if (w) svg.style.setProperty('--fig-w', Math.round(w) + 'px');
  }

  // 3. numbering
  const labels = new Map();
  const ids = new Set();
  for (const el of document.querySelectorAll('[id]')) {
    if (el.closest('.mermaid-svg') || el.closest('svg')) continue;
    if (ids.has(el.id)) errors.push('duplicate id: ' + el.id);
    ids.add(el.id);
  }
  const toc = [];
  let n2 = 0, app = 0;
  for (const sec of document.querySelectorAll('main > section')) {
    const h2 = sec.querySelector(':scope > h2');
    if (!h2) continue;
    if (sec.id) { h2.id = sec.id; sec.removeAttribute('id'); }
    if (!h2.id) h2.id = 'sec-' + slug(h2.textContent);
    let label = null, refText = null;
    if (sec.classList.contains('appendix')) { app++; label = String.fromCharCode(64 + app); refText = 'Appendix ' + label; }
    else if (!sec.classList.contains('nonum')) { n2++; label = String(n2); refText = '\u00a7' + label; }
    if (label) h2.insertAdjacentHTML('afterbegin', '<span class="secnum">' + label + '</span>');
    labels.set(h2.id, refText || h2.textContent);
    const entry = { id: h2.id, label, text: h2.textContent.replace(label || '', '').trim(), kids: [] };
    if (!sec.classList.contains('notoc')) toc.push(entry);
    let n3 = 0;
    for (const h3 of sec.querySelectorAll('h3')) {
      if (!h3.id) h3.id = h2.id + '-' + slug(h3.textContent);
      let l3 = null;
      if (label) { n3++; l3 = label + '.' + n3; h3.insertAdjacentHTML('afterbegin', '<span class="secnum">' + l3 + '</span>'); }
      labels.set(h3.id, l3 ? '\u00a7' + l3 : h3.textContent);
      entry.kids.push({ id: h3.id, label: l3, text: h3.textContent.replace(l3 || '', '').trim() });
    }
  }
  let nf = 0, nt = 0, ne = 0;
  for (const fig of document.querySelectorAll('main figure')) {
    const isTbl = fig.classList.contains('tbl');
    const num = isTbl ? ++nt : ++nf;
    const name = (isTbl ? 'Table ' : 'Figure ') + num;
    if (fig.id) labels.set(fig.id, name);
    const cap = fig.querySelector(':scope > figcaption');
    if (cap) cap.insertAdjacentHTML('afterbegin', '<span class="fig-num">' + name + '.</span>');
    else errors.push('figure without figcaption: ' + (fig.id || '(no id) ' + fig.innerHTML.slice(0, 60)));
  }
  for (const eq of document.querySelectorAll('main div.math[id]')) {
    ne++;
    // formula in its own scrolling box, number beside it (never overlapping a wide formula)
    const body = document.createElement('div');
    body.className = 'eq-body';
    body.append(...eq.childNodes);
    eq.append(body);
    eq.classList.add('numbered');
    eq.insertAdjacentHTML('beforeend', '<span class="eqno">(' + ne + ')</span>');
    labels.set(eq.id, 'Eq.\u00a0(' + ne + ')');
  }
  for (const a of document.querySelectorAll('main a.ref')) {
    const id = (a.getAttribute('href') || '').replace(/^#/, '');
    if (!labels.has(id) && !document.getElementById(id)) { errors.push('dangling ref: #' + id); continue; }
    if (!a.textContent.trim()) a.textContent = labels.get(id) || id;
  }
  for (const a of document.querySelectorAll('main a[href^="#"]:not(.ref)')) {
    const id = a.getAttribute('href').slice(1);
    if (id && !document.getElementById(id)) errors.push('dangling link: #' + id);
  }

  // 3c. packet tooltips: every 0xNN packet id explains itself on hover (packets.json). Text in
  // HTML gets <abbr class="pkt" title>; an SVG <text> (diagrams, charts) gets a <title> child.
  // Hex that is not a packet id is left alone: sizes ("0x12 bytes"), offsets ("+0x71"), flags
  // ("|0x80", "flag 0x40"), the sequence wrap ("0xFF → 1"). "sub 0xNN" is an 0xBF subcommand.
  const PKT = /0x([0-9A-Fa-f]{2})(?![0-9A-Fa-f])(?:\s+sub\s+(\d+))?/g;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: n =>
    n.parentElement.closest('head, script, style, title, .math, abbr.pkt') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const texts = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) texts.push(n);
  const before = (i, off) => { let s = texts[i].data.slice(0, off); for (let j = i - 1; j >= 0 && s.length < 12; j--) s = texts[j].data + s; return s.slice(-12); };
  const after = (i, off) => { let s = texts[i].data.slice(off); for (let j = i + 1; j < texts.length && s.length < 12; j++) s += texts[j].data; return s.slice(0, 12); };
  const plan = [];
  let pktSkipped = 0;
  texts.forEach((node, i) => {
    const hits = [];
    for (const m of node.data.matchAll(PKT)) {
      const id = '0x' + m[1].toUpperCase(), b = before(i, m.index), a = after(i, m.index + m[0].length);
      if (/[+|]\s*$|flag\s*$/.test(b) || /^(-byte|\s+bytes\b|\s*→)/.test(a)) { pktSkipped++; continue; }
      let tip;
      if (/sub\s*$/.test(b)) tip = PACKETS.bf_sub[id];  // text nodes join without spaces: "extended" + "sub "
      else {
        tip = PACKETS.ids[id];
        const sub = m[2] && (PACKETS.sub[id] || {})[m[2]];
        if (tip && sub) tip += ' ' + sub;
      }
      if (!tip) { errors.push('no packets.json tooltip for ' + id + ' in "' + (b + m[0] + a).replace(/\s+/g, ' ').trim() + '"'); continue; }
      hits.push([m.index, m[0].length, tip]);
    }
    if (hits.length) plan.push([node, hits]);
  });
  const svgTips = new Map();
  let pktTips = 0;
  for (const [node, hits] of plan) {
    const el = node.parentElement;
    if (el.closest('svg') && !el.closest('foreignObject')) {
      const text = el.closest('text');
      if (!text) continue;
      const set = svgTips.get(text) || new Set();
      hits.forEach(h => set.add(h[2]));
      svgTips.set(text, set);
      pktTips += hits.length;
      continue;
    }
    const frag = document.createDocumentFragment();
    let pos = 0;
    for (const [at, len, tip] of hits) {
      if (at > pos) frag.append(node.data.slice(pos, at));
      const ab = document.createElement('abbr');
      ab.className = 'pkt';
      ab.title = tip;
      ab.textContent = node.data.slice(at, at + len);
      frag.append(ab);
      pos = at + len;
      pktTips++;
    }
    if (pos < node.data.length) frag.append(node.data.slice(pos));
    node.replaceWith(frag);
  }
  for (const [text, set] of svgTips) {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    t.textContent = [...set].join('\n');
    text.prepend(t);
  }

  // 4. tables of contents
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const item = e => '<li><a href="#' + e.id + '">' + (e.label ? '<span class="num">' + e.label + '</span>' : '') + esc(e.text) + '</a>' +
    (e.kids && e.kids.length ? '<ol>' + e.kids.map(item).join('') + '</ol>' : '') + '</li>';
  const nav = document.getElementById('toc');
  if (nav) nav.innerHTML = '<p class="toc-title">Contents</p><ol>' + toc.map(item).join('') + '</ol>';
  for (const inl of document.querySelectorAll('.toc-inline')) {
    inl.innerHTML = '<ol>' + toc.map(e => '<li><a href="#' + e.id + '">' + (e.label ? '<span class="num">' + e.label + '</span>' : '') + esc(e.text) + '</a></li>').join('') + '</ol>';
  }

  document.querySelectorAll('head script').forEach(s => s.remove());
  const words = (document.querySelector('main').innerText.match(/\S+/g) || []).length;
  return { errors, words, figures: nf, tables: nt, equations: ne, sections: toc.length, diagrams: k, pktTips, pktSkipped };
}
"""


def run_charts(env: dict | None = None) -> None:
    for script in sorted(glob.glob(str(SRC / "charts" / "fig_*.py"))):
        r = subprocess.run([sys.executable, script], cwd=SRC, capture_output=True, text=True,
                           env={**os.environ, **env} if env else None)
        if r.returncode:
            sys.exit(f"chart script failed: {script}\n{r.stdout}\n{r.stderr}")
        print(f"chart  {os.path.basename(script)}")


def inline_includes(text: str, depth: int = 0, figdir: Path | None = None) -> str:
    """Inline <!--#include path--> (relative to SRC); figures/* come from figdir when given."""
    if depth > 4:
        sys.exit("include nesting too deep")

    def sub(m: re.Match) -> str:
        rel = m.group(1)
        p = figdir / rel[len("figures/"):] if figdir and rel.startswith("figures/") else SRC / rel
        if not p.is_file():
            sys.exit(f"missing include: {rel} ({p})")
        body = p.read_text(encoding="utf-8")
        body = re.sub(r"^\s*<\?xml[^>]*>\s*", "", body)
        body = re.sub(r"<!DOCTYPE[^>]*>\s*", "", body)
        return inline_includes(body, depth + 1, figdir)

    return INCLUDE.sub(sub, text)


def assemble(only: str | None = None, figdir: Path | None = None, edition: str = "") -> str:
    tpl = (SRC / "template.html").read_text(encoding="utf-8")
    hero = inline_includes((SRC / "hero.html").read_text(encoding="utf-8"), figdir=figdir)
    hero = hero.replace("<!--@@EDITION@@-->", f"\n  <div><b>Edition</b>{edition}</div>" if edition else "")
    sections = []
    files = sorted(glob.glob(str(SRC / "sections" / (f"{only}.html" if only and not only.endswith(".html") else only or "*.html"))))
    if not files:
        sys.exit(f"no sections match {only!r}")
    for f in files:
        sections.append(f"<!-- {os.path.basename(f)} -->\n" + inline_includes(Path(f).read_text(encoding="utf-8"), figdir=figdir))
    title = re.search(r"<h1[^>]*>(.*?)</h1>", hero, re.S)
    sub = re.search(r'class="subtitle"[^>]*>(.*?)</p>', hero, re.S)
    strip = lambda s: html.escape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(s))).strip())
    page = tpl.replace("@@STYLE@@", (SRC / "style.css").read_text(encoding="utf-8"))
    page = page.replace("@@TITLE@@", strip(title.group(1)) if title else "uo-harness")
    page = page.replace("@@DESCRIPTION@@", strip(sub.group(1)) if sub else "")
    page = page.replace("@@HERO@@", hero).replace("@@SECTIONS@@", "\n".join(sections))
    return page


def prerender(assembled: Path) -> tuple[str, dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        console = []
        page.on("pageerror", lambda e: console.append(str(e)))
        page.goto(assembled.as_uri())
        page.add_script_tag(path=str(SRC / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"))
        page.add_script_tag(path=str(SRC / "node_modules" / "temml" / "dist" / "temml.min.js"))
        page.evaluate("document.fonts.ready")
        stats = page.evaluate(PRERENDER_JS, json.loads((SRC / "packets.json").read_text(encoding="utf-8")))
        stats["errors"] += [f"page error: {c}" for c in console]
        out = page.content()
        browser.close()
    return out, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-charts", action="store_true", help="skip charts/fig_*.py")
    ap.add_argument("--only", metavar="GLOB", help="preview: build only sections/GLOB (e.g. '04-*'); "
                    "references into other sections become warnings")
    ap.add_argument("--out", metavar="PATH", help=f"output file (default {OUT.name}; use one per preview)")
    ap.add_argument("--pseudonymize", action="store_true",
                    help="build the pseudonymized edition (rules and default file name in pseudonyms.json)")
    args = ap.parse_args()
    rules = figdir = None
    if args.pseudonymize:
        from pseudonyms import ENV, Rules

        rules = Rules()
        figdir = Path(tempfile.mkdtemp(prefix="paper-pseudo-figures-"))
    out_path = Path(args.out).resolve() if args.out else (SRC.parent / rules.output if rules else OUT)
    fd, tmp = tempfile.mkstemp(suffix=".html", prefix="paper-assembling-")
    os.close(fd)
    assembled = Path(tmp)
    try:
        if rules:
            run_charts({"PAPER_FIGURES_DIR": str(figdir), ENV: str(rules.path)})
            # chart text must be replaced before layout, not by the page pass below, or the
            # longer pseudonyms overflow the figure
            for svg in sorted(figdir.glob("*.svg")):
                for leak in rules.leaks(svg.read_text(encoding="utf-8")):
                    sys.exit(f"chart {svg.name} was not pseudonymized at render time: {leak}")
            page = rules.apply(assemble(args.only, figdir, rules.notice))
        else:
            if not args.no_charts:
                run_charts()
            page = assemble(args.only)
        assembled.write_text(page, encoding="utf-8")
        out, stats = prerender(assembled)
    finally:
        assembled.unlink(missing_ok=True)
        if figdir:
            shutil.rmtree(figdir, ignore_errors=True)
    if not out.lstrip().lower().startswith("<!doctype"):
        out = "<!doctype html>\n" + out
    if rules:
        leaks = rules.leaks(out)
        for leak in leaks:
            print("LEAK ", leak)
        if leaks:
            sys.exit(f"refusing to write {out_path}: {len(leaks)} forbidden match(es); extend pseudonyms.json")
    out_path.write_text(out, encoding="utf-8")
    errors = stats["errors"]
    if args.only:
        for e in [e for e in errors if e.startswith("dangling")]:
            print("WARN ", e, "(outside --only)")
        errors = [e for e in errors if not e.startswith("dangling")]
    for e in errors:
        print("ERROR", e)
    print(f"wrote {out_path}: {out_path.stat().st_size/1024:.0f} KB, "
          f"{stats['words']} words, {stats['sections']} sections, {stats['figures']} figures "
          f"({stats['diagrams']} mermaid), {stats['tables']} tables, {stats['equations']} numbered equations, "
          f"{stats['pktTips']} packet tooltips ({stats['pktSkipped']} non-packet hex left alone)")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
