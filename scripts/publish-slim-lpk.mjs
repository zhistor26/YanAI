import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const projectRoot = path.resolve(import.meta.dirname, "..");
const skipDockerBuild = process.argv.includes("--skip-docker-build");
const skipCopyImage = process.argv.includes("--skip-copy-image");
const version = readPackageVersion();
const localImage = `cloud.lazycat.app.yanai:${version}`;
const copySource = process.env.YANAI_COPY_IMAGE_SOURCE?.trim() || localImage;
const registryImage =
  process.env.YANAI_REGISTRY_IMAGE?.trim() || `registry.lazycat.cloud/u64111927/zhistor/yanai:${version}`;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: projectRoot,
    stdio: "inherit",
    shell: process.platform === "win32",
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with exit code ${result.status ?? "unknown"}`);
  }
}

function readPackageVersion() {
  const packageYml = fs.readFileSync(path.join(projectRoot, "package.yml"), "utf8");
  const matched = packageYml.match(/^version:\s*([^\s#]+)/m);
  if (!matched?.[1]) {
    throw new Error("failed to read version from package.yml");
  }
  return matched[1].trim();
}

function extractRegistryImage(output) {
  const matched = String(output || "").match(/registry\.lazycat\.cloud\/[^\s'"]+/);
  return matched?.[0] ?? "";
}

function ensureManifestImage(imageRef) {
  const manifestPath = path.join(projectRoot, "lzc-manifest.yml");
  const manifest = fs.readFileSync(manifestPath, "utf8");
  const next = manifest.replace(/^\s*image:\s*.+$/m, `    image: ${imageRef}`);
  if (next === manifest) {
    throw new Error("failed to update services.yanai.image in lzc-manifest.yml");
  }
  fs.writeFileSync(manifestPath, next, "utf8");
  console.log(`manifest image -> ${imageRef}`);
}

console.log("1/4 prepare source tar");
run("node", ["scripts/prepare-lpk-build.mjs"]);

if (!skipDockerBuild) {
  console.log(`2/4 docker build ${localImage}`);
  run("docker", ["build", "--platform", "linux/amd64", "-t", localImage, "."]);
} else {
  console.log(`2/4 skip docker build (${localImage})`);
}

let copiedImage = registryImage;
if (!skipCopyImage) {
  console.log(`3/4 copy image to lazycat registry from ${copySource}`);
  console.log(
    "    copy-image 需要公网可拉取镜像：先 docker login && docker push，再设置 YANAI_COPY_IMAGE_SOURCE 重试。",
  );
  const copyResult = spawnSync("lzc-cli", ["appstore", "copy-image", copySource, "--trace-level", "quiet"], {
    cwd: projectRoot,
    encoding: "utf8",
    shell: process.platform === "win32",
  });
  if (copyResult.stdout) process.stdout.write(copyResult.stdout);
  if (copyResult.stderr) process.stderr.write(copyResult.stderr);
  if (copyResult.status !== 0) {
    throw new Error(
      `copy-image failed. Example:\n  docker tag ${localImage} <your-dockerhub>/yanai:${version}\n  docker push <your-dockerhub>/yanai:${version}\n  set YANAI_COPY_IMAGE_SOURCE=<your-dockerhub>/yanai:${version}\n  node scripts/publish-slim-lpk.mjs --skip-docker-build`,
    );
  }
  copiedImage = extractRegistryImage(copyResult.stdout) || registryImage;
} else {
  console.log("3/4 skip copy-image");
}

ensureManifestImage(copiedImage);

console.log("4/4 build slim LPK (manifest + content only)");
run("lzc-cli", ["project", "build"]);

const lpkPath = path.join(projectRoot, `cloud.lazycat.app.yanai-v${version}.lpk`);
if (fs.existsSync(lpkPath)) {
  const sizeMb = (fs.statSync(lpkPath).size / (1024 * 1024)).toFixed(2);
  console.log(`done: ${lpkPath} (${sizeMb} MiB)`);
}
