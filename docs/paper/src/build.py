"""Build docs/paper/uo-harness-paper.html: one self-contained page, no network, no runtime libraries.

    python docs/paper/src/build.py [--no-charts] [--only 'NN-*' --out PREVIEW.html]

Steps:
  1. run every charts/fig_*.py (each writes figures/<name>.svg from data/*.json)
  2. assemble template.html + style.css + hero.html + sections/*.html (sorted), inlining
     <!--#include path--> directives (paths relative to this directory)
  3. prerender in headless Chromium (Playwright): mermaid <pre class="mermaid"> -> inline SVG,
     .math elements (TeX) -> MathML via temml, section/figure/table/equation numbering,
     <a class="ref" href="#id"></a> cross-references, the tables of contents
  4. strip the build-time scripts and write the static page

Needs: pip install playwright && python -m playwright install chromium; `bun install` in this dir
(mermaid + temml). Fails on a broken diagram, bad TeX, a duplicate id or a dangling reference.
"""
from __future__ import annotations

import argparse
import glob
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(os.path.dirname(os.path.abspath(__file__)))
OUT = SRC.parent / "uo-harness-paper.html"
INCLUDE = re.compile(r"<!--#include\s+(\S+?)\s*-->")

PRERENDER_JS = r"""
async () => {
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
    startOnLoad: false, theme: 'base', fontFamily: sans, securityLevel: 'loose',
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
    // a raw <br/> in the HTML source parses as an element; give mermaid its text form back
    pre.querySelectorAll('br').forEach(b => b.replaceWith(document.createTextNode('<br/>')));
    const src = pre.textContent;
    const where = pre.closest('figure')?.id || pre.closest('section')?.id || '?';
    try {
      const { svg } = await mermaid.render('mmd' + (k++), src);
      const div = document.createElement('div');
      div.className = 'mermaid-svg';
      div.innerHTML = svg;
      pre.replaceWith(div);
    } catch (e) { errors.push('mermaid in #' + where + ': ' + (e.message || e).toString().slice(0, 300)); }
  }
  document.querySelectorAll('body > svg[id^="dmmd"], body > div[id^="dmmd"]').forEach(n => n.remove());

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
  return { errors, words, figures: nf, tables: nt, equations: ne, sections: toc.length, diagrams: k };
}
"""


def run_charts() -> None:
    for script in sorted(glob.glob(str(SRC / "charts" / "fig_*.py"))):
        r = subprocess.run([sys.executable, script], cwd=SRC, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"chart script failed: {script}\n{r.stdout}\n{r.stderr}")
        print(f"chart  {os.path.basename(script)}")


def inline_includes(text: str, depth: int = 0) -> str:
    if depth > 4:
        sys.exit("include nesting too deep")

    def sub(m: re.Match) -> str:
        p = SRC / m.group(1)
        if not p.is_file():
            sys.exit(f"missing include: {m.group(1)}")
        body = p.read_text(encoding="utf-8")
        body = re.sub(r"^\s*<\?xml[^>]*>\s*", "", body)
        body = re.sub(r"<!DOCTYPE[^>]*>\s*", "", body)
        return inline_includes(body, depth + 1)

    return INCLUDE.sub(sub, text)


def assemble(only: str | None = None) -> str:
    tpl = (SRC / "template.html").read_text(encoding="utf-8")
    hero = inline_includes((SRC / "hero.html").read_text(encoding="utf-8"))
    sections = []
    files = sorted(glob.glob(str(SRC / "sections" / (f"{only}.html" if only and not only.endswith(".html") else only or "*.html"))))
    if not files:
        sys.exit(f"no sections match {only!r}")
    for f in files:
        sections.append(f"<!-- {os.path.basename(f)} -->\n" + inline_includes(Path(f).read_text(encoding="utf-8")))
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
        stats = page.evaluate(PRERENDER_JS)
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
    args = ap.parse_args()
    out_path = Path(args.out).resolve() if args.out else OUT
    if not args.no_charts:
        run_charts()
    fd, tmp = tempfile.mkstemp(suffix=".html", prefix="paper-assembling-")
    os.close(fd)
    assembled = Path(tmp)
    try:
        assembled.write_text(assemble(args.only), encoding="utf-8")
        out, stats = prerender(assembled)
    finally:
        assembled.unlink(missing_ok=True)
    if not out.lstrip().lower().startswith("<!doctype"):
        out = "<!doctype html>\n" + out
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
          f"({stats['diagrams']} mermaid), {stats['tables']} tables, {stats['equations']} numbered equations")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
