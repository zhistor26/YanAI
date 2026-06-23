import type { StoredImage } from "@/store/image-conversations";
import { isLocalImagePath, resolveApiAssetUrl } from "@/lib/assets";
import { getStoredAuthKey } from "@/store/auth";

const imageBlobCache = new Map<string, Promise<Blob>>();

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

function blobCacheKey(source: string, recordId = "") {
  return `${recordId}::${source.trim()}`;
}

function dataUrlToBlob(dataUrl: string) {
  const [header, base64] = dataUrl.split(",");
  const mime = header.match(/^data:(.*?);base64$/)?.[1] || "image/png";
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: mime });
}

export function prefetchImageBlob(source: string, recordId = "") {
  const normalized = source.trim();
  if (!normalized || normalized.startsWith("data:")) {
    return;
  }
  const key = blobCacheKey(normalized, recordId);
  if (imageBlobCache.has(key)) {
    return;
  }
  imageBlobCache.set(
    key,
    imageSourceToBlob(normalized, recordId).catch((error) => {
      imageBlobCache.delete(key);
      throw error;
    }),
  );
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
  const normalized = source.trim();
  if (!normalized) {
    throw new Error("图片缺少可保存的数据");
  }

  if (normalized.startsWith("data:")) {
    return dataUrlToBlob(normalized);
  }

  const cacheKey = blobCacheKey(normalized, recordId);
  const cached = imageBlobCache.get(cacheKey);
  if (cached) {
    return cached;
  }

  const load = (async () => {
    const resolved = resolveApiAssetUrl(normalized);

    if (isLocalImagePath(normalized)) {
      const headers = await buildAuthHeaders();
      const response = await fetch(resolved, {
        credentials: "include",
        headers,
        cache: "force-cache",
      });
      if (response.ok) {
        return response.blob();
      }
      if (recordId) {
        return fetchMyImageBlob(recordId, normalized);
      }
      throw new Error(`读取图片失败 (${response.status})`);
    }

    if (isSameOriginUrl(resolved)) {
      const response = await fetch(resolved, { credentials: "include", cache: "force-cache" });
      if (response.ok) {
        return response.blob();
      }
    }

    return fetchMyImageBlob(recordId, normalized);
  })();

  imageBlobCache.set(cacheKey, load);
  try {
    return await load;
  } catch (error) {
    imageBlobCache.delete(cacheKey);
    throw error;
  }
}

export async function storedImageToBlob(image: StoredImage) {
  const recordId = image.record_id || "";
  if (image.b64_json) {
    return imageSourceToBlob(`data:image/png;base64,${image.b64_json}`, recordId);
  }
  if (image.url) {
    return imageSourceToBlob(image.url, recordId);
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

export type SaveImageOutcome = "saved" | "cancelled";

export function isUserCancelledSave(error: unknown) {
  if (!error) {
    return false;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return true;
  }
  const message = (error instanceof Error ? error.message : String(error)).toLowerCase();
  return (
    message.includes("abort") ||
    message.includes("cancel") ||
    message.includes("dismiss") ||
    message.includes("用户取消")
  );
}

async function writeBlobToPicker(blob: Blob, fileName: string) {
  const picker = window.showSaveFilePicker?.bind(window);
  if (!picker) {
    triggerLazyCatDownload(blob, fileName);
    return;
  }
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
}

function canOpenSavePickerFirst(source: string) {
  const normalized = source.trim();
  return Boolean(
    normalized &&
      (normalized.startsWith("data:") ||
        isLocalImagePath(normalized) ||
        (normalized.startsWith("http") && isSameOriginUrl(normalized))),
  );
}

export async function saveStoredImageToDisk(image: StoredImage, fileName: string): Promise<SaveImageOutcome> {
  try {
    const source = image.b64_json ? `data:image/png;base64,${image.b64_json}` : image.url || "";
    if (canOpenSavePickerFirst(source) && window.showSaveFilePicker) {
      const handlePromise = window.showSaveFilePicker({
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
      const blob = await storedImageToBlob(image);
      const handle = await handlePromise;
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return "saved";
    }
    const blob = await storedImageToBlob(image);
    await writeBlobToPicker(blob, fileName);
    return "saved";
  } catch (error) {
    if (isUserCancelledSave(error)) {
      return "cancelled";
    }
    throw error;
  }
}

export async function saveImageSourceToDisk(
  source: string,
  fileName: string,
  recordId = "",
): Promise<SaveImageOutcome> {
  try {
    if (canOpenSavePickerFirst(source) && window.showSaveFilePicker) {
      const handlePromise = window.showSaveFilePicker({
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
      const blob = await imageSourceToBlob(source, recordId);
      const handle = await handlePromise;
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return "saved";
    }
    const blob = await imageSourceToBlob(source, recordId);
    await writeBlobToPicker(blob, fileName);
    return "saved";
  } catch (error) {
    if (isUserCancelledSave(error)) {
      return "cancelled";
    }
    throw error;
  }
}

export function consumePendingLazyCatReferenceFile() {
  const pending = (window as Window & { __yanaiPendingReferenceFile?: File }).__yanaiPendingReferenceFile;
  if (!(pending instanceof File)) {
    return null;
  }
  delete (window as Window & { __yanaiPendingReferenceFile?: File }).__yanaiPendingReferenceFile;
  return pending;
}
