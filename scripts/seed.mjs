import { spawnSync } from "node:child_process";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const seedDirectory = join(root, "seed");
const outputDirectory = join(root, ".seed");
const remote = process.argv.includes("--remote");
const local = !remote;

const files = (await readdir(seedDirectory))
  .filter((name) => name.endsWith(".json"))
  .sort();

if (files.length === 0) {
  console.error("No seed JSON files found in seed/");
  process.exit(1);
}

const entries = [];

for (const file of files) {
  const id = file.replace(/\.json$/, "");
  const raw = await readFile(join(seedDirectory, file), "utf8");
  let data;

  try {
    data = JSON.parse(raw);
  } catch (error) {
    console.error(`${file}: invalid JSON - ${error.message}`);
    process.exit(1);
  }

  if (!data || typeof data !== "object" || !data.metadata || typeof data.article !== "string") {
    console.error(`${file}: expected {"metadata": {...}, "article": "..."}`);
    process.exit(1);
  }

  entries.push({ key: id, value: JSON.stringify(data) });
}

await mkdir(outputDirectory, { recursive: true });

const bulkFile = join(outputDirectory, "bulk.json");
await writeFile(bulkFile, `${JSON.stringify(entries, null, 2)}\n`);

const wrangler = join(root, "node_modules", "wrangler", "bin", "wrangler.js");
const args = [
  wrangler,
  "kv",
  "bulk",
  "put",
  bulkFile,
  "--binding",
  "BLOG_KV",
  local ? "--local" : "--remote",
];

console.log(`Seeding ${entries.length} article(s) to ${local ? "local" : "remote"} KV...`);

const result = spawnSync(process.execPath, args, {
  cwd: root,
  stdio: "inherit",
});

process.exit(result.status ?? 1);
