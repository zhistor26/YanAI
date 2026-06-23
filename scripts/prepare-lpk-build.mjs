import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const projectRoot = path.resolve(import.meta.dirname, "..");
const iconTarget = path.join(projectRoot, "icon.png");
const sourceTar = path.join(projectRoot, "yanai-src.tar.gz");

const ROOTS = ["web", "api", "services", "utils", "scripts", "test"];
const ROOT_FILES = ["pyproject.toml", "uv.lock", "main.py", "VERSION", "config.example.json"];
const EXCLUDE_PARTS = [
  `${path.sep}node_modules${path.sep}`,
  `${path.sep}.next${path.sep}`,
  `${path.sep}out${path.sep}`,
  `${path.sep}__pycache__${path.sep}`,
  `${path.sep}.pytest_cache${path.sep}`,
  `${path.sep}.venv${path.sep}`,
];

function shouldExclude(relPath) {
  const normalized = relPath.split("/").join(path.sep);
  return EXCLUDE_PARTS.some((part) => normalized.includes(part)) || normalized.endsWith(".pyc");
}

function walkDir(absDir, relDir, out) {
  for (const entry of fs.readdirSync(absDir, { withFileTypes: true })) {
    const rel = relDir ? `${relDir}/${entry.name}` : entry.name;
    const abs = path.join(absDir, entry.name);
    if (shouldExclude(rel)) continue;
    if (entry.isDirectory()) {
      walkDir(abs, rel, out);
    } else if (entry.isFile()) {
      out.push(rel.replace(/\\/g, "/"));
    }
  }
}

function collectSourceFiles() {
  const files = [];
  for (const name of ROOT_FILES) {
    const abs = path.join(projectRoot, name);
    if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
      files.push(name);
    }
  }
  for (const root of ROOTS) {
    const abs = path.join(projectRoot, root);
    if (fs.existsSync(abs) && fs.statSync(abs).isDirectory()) {
      walkDir(abs, root, files);
    }
  }
  return [...new Set(files)].sort();
}

function generateIcon() {
  const code = [
    "from PIL import Image, ImageDraw, ImageFont",
    `out = ${JSON.stringify(iconTarget)}`,
    "im = Image.new('RGB', (400, 400), '#fff7ed')",
    "draw = ImageDraw.Draw(im)",
    "draw.rounded_rectangle((40, 40, 360, 360), radius=48, fill='#f97316')",
    "draw.rounded_rectangle((96, 96, 304, 304), radius=36, fill='#fffbeb')",
    "try:",
    "    font = ImageFont.truetype('arial.ttf', 120)",
    "except OSError:",
    "    font = ImageFont.load_default()",
    "draw.text((118, 118), '颜', fill='#9a3412', font=font)",
    "im.save(out, format='PNG', optimize=True, compress_level=9)",
  ].join("\n");

  const result = spawnSync("python", ["-c", code], { encoding: "utf8", stdio: "pipe" });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    throw new Error(result.stderr || "Failed to generate icon.png");
  }
  console.log(`icon -> icon.png (${fs.statSync(iconTarget).size} bytes, 400x400)`);
}

function createSourceTar(files) {
  if (!files.includes("web/src/app/page.tsx")) {
    throw new Error("web/src/app/page.tsx missing from source tar inputs");
  }
  if (fs.existsSync(sourceTar)) {
    fs.rmSync(sourceTar, { force: true });
  }

  const helper = path.join(projectRoot, ".prepare-source-tar.py");
  const code = [
    "import json",
    "import os",
    "import tarfile",
    `root = ${JSON.stringify(projectRoot)}`,
    `out = ${JSON.stringify(sourceTar)}`,
    `files = json.loads(${JSON.stringify(JSON.stringify(files))})`,
    "with tarfile.open(out, 'w:gz') as archive:",
    "    for rel in files:",
    "        archive.add(os.path.join(root, rel.replace('/', os.sep)), arcname=rel.replace('\\\\', '/'))",
    "print(f'source tar created: {len(files)} files')",
  ].join("\n");
  fs.writeFileSync(helper, code, "utf8");

  const result = spawnSync("python", [helper], { encoding: "utf8", stdio: "pipe" });
  fs.rmSync(helper, { force: true });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || "failed to create yanai-src.tar.gz");
  }
  console.log(`source tar -> yanai-src.tar.gz (${files.length} files, ${fs.statSync(sourceTar).size} bytes)`);
}

generateIcon();
createSourceTar(collectSourceFiles());
