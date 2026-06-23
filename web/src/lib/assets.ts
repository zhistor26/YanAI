import webConfig from "@/constants/common-env";

const API_ASSET_PREFIXES = ["/images/", "/prompt-assets/"];

export function isLocalImagePath(value?: string | null) {
  const url = String(value || "").trim();
  if (!url || url.startsWith("data:")) {
    return false;
  }
  if (url.startsWith("/images/")) {
    return true;
  }
  try {
    return new URL(url, typeof window !== "undefined" ? window.location.origin : "http://localhost").pathname.startsWith(
      "/images/",
    );
  } catch {
    return false;
  }
}

export function resolveApiAssetUrl(value?: string | null) {
  const url = String(value || "").trim();
  if (!url || !API_ASSET_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    return url;
  }

  const apiBase = webConfig.apiUrl.replace(/\/$/, "");
  return apiBase ? `${apiBase}${url}` : url;
}
