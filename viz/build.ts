// Bundles src/main.tsx (+ imported CSS) into dist/assets/main.{js,css} and copies
// index.html into dist/. `--watch` rebuilds when src/ or index.html change.
import { copyFileSync, mkdirSync, rmSync, watch } from "node:fs";
import { join } from "node:path";

const root = import.meta.dir;
const dist = join(root, "dist");

async function build(): Promise<boolean> {
  const started = performance.now();
  const result = await Bun.build({
    entrypoints: [join(root, "src/main.tsx")],
    outdir: join(dist, "assets"),
    naming: "[name].[ext]",
    minify: true,
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    target: "browser",
  });
  if (!result.success) {
    for (const log of result.logs) console.error(log);
    return false;
  }
  copyFileSync(join(root, "index.html"), join(dist, "index.html"));
  const outs = result.outputs.map((o) => o.path.slice(dist.length + 1)).join(", ");
  console.log(`built ${outs} + index.html in ${Math.round(performance.now() - started)} ms`);
  return true;
}

rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, "assets"), { recursive: true });
const ok = await build();

if (process.argv.includes("--watch")) {
  let timer: Timer | undefined;
  const again = () => {
    clearTimeout(timer);
    timer = setTimeout(() => void build(), 100);
  };
  watch(join(root, "src"), { recursive: true }, again);
  watch(join(root, "index.html"), again);
  console.log("watching src/ and index.html");
} else if (!ok) {
  process.exit(1);
}
