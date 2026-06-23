import type { StoredImage } from "@/store/image-conversations";
import { resolveApiAssetUrl } from "@/lib/assets";
import { getStoredAuthKey } from "@/store/auth";

const LAZY_CAT_DISK_PREFIX = "/_lzc/files/home";
export const LAZY_CAT_GENERATED_DIR = "/YanAI/generated";

type SavePickerOptions = {
  suggestedName?: string;
  types?: Array<{
    description?: string;
    accept: Record<string, string[]>;
  }>;
};

type SaveFileHandle = {
  createWritable: () => Promise<{
    write: (data: Blob) => Promise<void>;
    close: () => Promise<void>;
  }>;
};

declare global {
  interface Window {
    showSaveFilePicker?: (options?: SavePickerOptions) => Promise<SaveFileHandle>;
  }
}

export function normalizeLazyCatPath(path: string) {
  let normalized = String(path || "")
    .trim()
    .replace(/\.$/, "");
  normalized = normalized.replace(/^\/_lzc\/files\/home(?=\/|$)/, "");
  if (normalized && !normalized.startsWith("/")) {
    normalized = `/${normalized}`;
  }
  return normalized;
}

export function isLazyCatDriveContext() {
  if (typeof window === "undefined") {
    return false;
  }
  return window.location.pathname.startsWith("/") && !window.location.hostname.includes("localhost");
}

export async function probeLazyCatDrive() {
  try {
    const response = await fetch(`${LAZY_CAT_DISK_PREFIX}/`, {
      method: "HEAD",
      credentials: "include",
    });
    return response.ok || response.status === 404 || response.status === 405;
  } catch {
    return false;
  }
}

function sanitizeFileName(name: string) {
  const cleaned = name.replace(/[\\/:*?"<>|]+/g, "-").trim();
  return cleaned || "image.png";
}

function isSameOriginUrl(url: string) {
  if (typeof window === "undefined" || !url.startsWith("http")) {
    return true;
  }
  try {
    return new URL(url).origin === window.location.origin;
  } catch {
    return false;
  }
}

async function buildAuthHeaders() {
  const headers: Record<string, string> = {};
  const authKey = await getStoredAuthKey();
  if (authKey) {
    headers.Authorization = `Bearer ${authKey}`;
  }
  return headers;
}

export async function fetchMyImageBlob(recordId: string, url: string) {
  const params = new URLSearchParams();
  if (recordId.trim()) {
    params.set("record_id", recordId.trim());
  }
  if (url.trim()) {
    params.set("url", url.trim());
  }
  const response = await fetch(`/api/me/images/file?${params.toString()}`, {
    credentials: "include",
    headers: await buildAuthHeaders(),
  });
  if (!response.ok) {
    throw new Error(`读取图片失败 (${response.status})`);
  }
  return response.blob();
}

export async function imageSourceToBlob(source: string, recordId = "") {
  const resolved = source.startsWith("data:") ? source : resolveApiAssetUrl(source);
  if (isSameOriginUrl(resolved)) {
    try {
      const response = await fetch(resolved, { credentials: "include" });
      if (response.ok) {
        return response.blob();
      }
    } catch {
      // Fall back to authenticated API proxy for cross-origin or blocked fetches.
    }
  }
  if (recordId || source) {
    return fetchMyImageBlob(recordId, source);
  }
  const response = await fetch(resolved, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`读取图片失败 (${response.status})`);
  }
  return response.blob();
}

export async function storedImageToBlob(image: StoredImage) {
  if (image.url) {
    return imageSourceToBlob(image.url);
  }
  if (image.b64_json) {
    return imageSourceToBlob(`data:image/png;base64,${image.b64_json}`);
  }
  throw new Error("图片缺少可保存的数据");
}

export async function putLazyCatFile(remotePath: string, blob: Blob) {
  const normalized = normalizeLazyCatPath(remotePath);
  const response = await fetch(`${LAZY_CAT_DISK_PREFIX}${normalized}`, {
    method: "PUT",
    credentials: "include",
    headers: {
      "Content-Type": blob.type || "application/octet-stream",
    },
    body: blob,
  });
  if (!response.ok) {
    throw new Error(`保存到懒猫网盘失败 (${response.status})`);
  }
  return normalized;
}

async function createLazyCatDirectory(remotePath: string) {
  const normalized = normalizeLazyCatPath(remotePath);
  if (!normalized || normalized === "/") {
    return;
  }

  const response = await fetch(`${LAZY_CAT_DISK_PREFIX}${normalized}`, {
    method: "MKCOL",
    credentials: "include",
  });
  if (response.ok || response.status === 405 || response.status === 409) {
    return;
  }
  throw new Error(`创建懒猫网盘目录失败 (${response.status})`);
}

async function ensureLazyCatDirectory(remotePath: string) {
  const normalized = normalizeLazyCatPath(remotePath);
  const segments = normalized.split("/").filter(Boolean);
  let current = "";

  for (const segment of segments) {
    current = `${current}/${segment}`;
    await createLazyCatDirectory(current);
  }
}

async function ensureLazyCatDirectoryBestEffort(remotePath: string) {
  try {
    await ensureLazyCatDirectory(remotePath);
  } catch (error) {
    console.warn("[lazycat-drive] ensure directory failed", error);
  }
}

export function buildGeneratedImagePath(fileName: string) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `${LAZY_CAT_GENERATED_DIR}/${stamp}-${sanitizeFileName(fileName)}`;
}

export async function autoSaveGeneratedImage(image: StoredImage, fileName: string) {
  const available = await probeLazyCatDrive();
  if (!available) {
    return null;
  }
  const blob = await storedImageToBlob(image);
  const remotePath = buildGeneratedImagePath(fileName.endsWith(".png") ? fileName : `${fileName}.png`);
  await ensureLazyCatDirectoryBestEffort(LAZY_CAT_GENERATED_DIR);
  return putLazyCatFile(remotePath, blob);
}

export function triggerLazyCatDownload(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = sanitizeFileName(fileName);
  link.style.display = "none";
  (document.body || document.documentElement).appendChild(link);
  link.click();
  window.setTimeout(() => {
    link.remove();
    URL.revokeObjectURL(url);
  }, 2000);
}

export async function saveBlobWithPicker(blob: Blob, fileName: string) {
  const picker = window.showSaveFilePicker?.bind(window);
  if (picker) {
    const handle = await picker({
      suggestedName: sanitizeFileName(fileName),
      types: [
        {
          description: "Image",
          accept: {
            "image/*": [".png", ".jpg", ".jpeg", ".webp", ".gif"],
          },
        },
      ],
    });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return;
  }
  triggerLazyCatDownload(blob, fileName);
}

export async function downloadStoredImageViaLazyCat(image: StoredImage, fileName: string) {
  const blob = await storedImageToBlob(image);
  await saveBlobWithPicker(blob, fileName);
}

export async function downloadSourceViaLazyCat(source: string, fileName: string, recordId = "") {
  const blob = await imageSourceToBlob(source, recordId);
  await saveBlobWithPicker(blob, fileName);
}

export function consumePendingLazyCatReferenceFile() {
  const pending = (window as Window & { __yanaiPendingReferenceFile?: File }).__yanaiPendingReferenceFile;
  if (!(pending instanceof File)) {
    return null;
  }
  delete (window as Window & { __yanaiPendingReferenceFile?: File }).__yanaiPendingReferenceFile;
  return pending;
}
