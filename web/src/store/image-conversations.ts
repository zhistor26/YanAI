"use client";

import localforage from "localforage";

import type { ImageModel } from "@/lib/api";

export type ImageConversationMode = "generate" | "edit";

export type StoredReferenceImage = {
  name: string;
  type: string;
  dataUrl: string;
};

export type StoredImage = {
  id: string;
  status?: "loading" | "success" | "error";
  b64_json?: string;
  url?: string;
  error?: string;
};

export type ImageTurnStatus = "queued" | "generating" | "success" | "error";

export type ImageTurn = {
  id: string;
  prompt: string;
  model: ImageModel;
  mode: ImageConversationMode;
  referenceImages: StoredReferenceImage[];
  count: number;
  size: string;
  images: StoredImage[];
  createdAt: string;
  status: ImageTurnStatus;
  error?: string;
};

export type ImageConversation = {
  id: string;
  ownerKey?: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns: ImageTurn[];
};

export type ImageConversationOwner = {
  role?: string | null;
  subjectId?: string | null;
  email?: string | null;
  name?: string | null;
};

export type ImageConversationStats = {
  queued: number;
  running: number;
};

const imageConversationStorage = localforage.createInstance({
  name: "chatgpt2api",
  storeName: "image_conversations",
});

const IMAGE_CONVERSATIONS_KEY = "items";
export const IMAGE_CONVERSATIONS_CHANGED_EVENT = "chatgpt2api:image-conversations-changed";
let imageConversationWriteQueue: Promise<void> = Promise.resolve();

export type ImageConversationsChangedDetail = {
  ownerKey: string;
};

function encodeStorageSegment(value: string) {
  return encodeURIComponent(value.trim());
}

function normalizeOwnerKey(ownerKey?: string | null) {
  return String(ownerKey || "").trim();
}

function getImageConversationsStorageKey(ownerKey?: string | null) {
  const normalizedOwnerKey = normalizeOwnerKey(ownerKey);
  return normalizedOwnerKey ? `${IMAGE_CONVERSATIONS_KEY}:${normalizedOwnerKey}` : IMAGE_CONVERSATIONS_KEY;
}

function emitImageConversationsChanged(ownerKey: string) {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<ImageConversationsChangedDetail>(IMAGE_CONVERSATIONS_CHANGED_EVENT, {
      detail: { ownerKey },
    }),
  );
}

export function getImageConversationOwnerKey(owner: ImageConversationOwner | null | undefined) {
  const subject = String(owner?.subjectId || owner?.email || owner?.name || "").trim();
  if (!subject) {
    return "";
  }

  const role = String(owner?.role || "unknown").trim() || "unknown";
  return `${encodeStorageSegment(role)}:${encodeStorageSegment(subject)}`;
}

function normalizeStoredImage(image: StoredImage): StoredImage {
  const normalized: StoredImage = {
    ...image,
    status:
      image.status === "loading" || image.status === "error" || image.status === "success"
        ? image.status
        : image.b64_json || image.url
          ? "success"
          : "loading",
  };
  if (normalized.url && normalized.b64_json) {
    const withoutBase64 = { ...normalized };
    delete withoutBase64.b64_json;
    return withoutBase64;
  }
  return normalized;
}

function normalizeReferenceImage(image: StoredReferenceImage): StoredReferenceImage {
  return {
    name: image.name || "reference.png",
    type: image.type || "image/png",
    dataUrl: image.dataUrl,
  };
}

function dataUrlMimeType(dataUrl: string) {
  const match = dataUrl.match(/^data:(.*?);base64,/);
  return match?.[1] || "image/png";
}

function getLegacyReferenceImages(source: Record<string, unknown>): StoredReferenceImage[] {
  if (Array.isArray(source.referenceImages)) {
    return source.referenceImages
      .filter((image): image is StoredReferenceImage => {
        if (!image || typeof image !== "object") {
          return false;
        }
        const candidate = image as StoredReferenceImage;
        return typeof candidate.dataUrl === "string" && candidate.dataUrl.length > 0;
      })
      .map(normalizeReferenceImage);
  }

  if (source.sourceImage && typeof source.sourceImage === "object") {
    const image = source.sourceImage as { dataUrl?: unknown; fileName?: unknown };
    if (typeof image.dataUrl === "string" && image.dataUrl) {
      return [
        {
          name: typeof image.fileName === "string" && image.fileName ? image.fileName : "reference.png",
          type: dataUrlMimeType(image.dataUrl),
          dataUrl: image.dataUrl,
        },
      ];
    }
  }

  return [];
}

function normalizeTurn(turn: ImageTurn & Record<string, unknown>): ImageTurn {
  const normalizedImages = Array.isArray(turn.images) ? turn.images.map(normalizeStoredImage) : [];
  const derivedStatus: ImageTurnStatus =
    normalizedImages.some((image) => image.status === "loading")
      ? "generating"
      : normalizedImages.some((image) => image.status === "error")
        ? "error"
        : "success";

  return {
    id: String(turn.id || `${Date.now()}`),
    prompt: String(turn.prompt || ""),
    model: (turn.model as ImageModel) || "gpt-image-2",
    mode: turn.mode === "edit" ? "edit" : "generate",
    referenceImages: getLegacyReferenceImages(turn),
    count: Math.max(1, Number(turn.count || normalizedImages.length || 1)),
    size: typeof turn.size === "string" ? turn.size : "",
    images: normalizedImages,
    createdAt: String(turn.createdAt || new Date().toISOString()),
    status:
      turn.status === "queued" ||
      turn.status === "generating" ||
      turn.status === "success" ||
      turn.status === "error"
        ? turn.status
        : derivedStatus,
    error: typeof turn.error === "string" ? turn.error : undefined,
  };
}

function normalizeConversation(
  conversation: ImageConversation & Record<string, unknown>,
  ownerKey = "",
): ImageConversation {
  const turns = Array.isArray(conversation.turns)
    ? conversation.turns.map((turn) => normalizeTurn(turn as ImageTurn & Record<string, unknown>))
    : [
        normalizeTurn({
          id: String(conversation.id || `${Date.now()}`),
          prompt: String(conversation.prompt || ""),
          model: (conversation.model as ImageModel) || "gpt-image-2",
          mode: conversation.mode === "edit" ? "edit" : "generate",
          referenceImages: getLegacyReferenceImages(conversation),
          count: Number(conversation.count || 1),
          size: typeof conversation.size === "string" ? conversation.size : "",
          images: Array.isArray(conversation.images) ? (conversation.images as StoredImage[]) : [],
          createdAt: String(conversation.createdAt || new Date().toISOString()),
          status:
            conversation.status === "generating" || conversation.status === "success" || conversation.status === "error"
              ? conversation.status
              : "success",
          error: typeof conversation.error === "string" ? conversation.error : undefined,
        }),
      ];
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  return {
    id: String(conversation.id || `${Date.now()}`),
    ownerKey: String(conversation.ownerKey || ownerKey || "").trim() || undefined,
    title: String(conversation.title || ""),
    createdAt: String(conversation.createdAt || lastTurn?.createdAt || new Date().toISOString()),
    updatedAt: String(conversation.updatedAt || lastTurn?.createdAt || new Date().toISOString()),
    turns,
  };
}

function sortImageConversations(conversations: ImageConversation[]): ImageConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function pickLatestConversation(current: ImageConversation, next: ImageConversation) {
  return getTimestamp(next.updatedAt) >= getTimestamp(current.updatedAt) ? next : current;
}

function queueImageConversationWrite<T>(operation: () => Promise<T>): Promise<T> {
  const result = imageConversationWriteQueue.then(operation);
  imageConversationWriteQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function readStoredImageConversations(ownerKey = ""): Promise<ImageConversation[]> {
  const normalizedOwnerKey = normalizeOwnerKey(ownerKey);
  const items =
    (await imageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
      getImageConversationsStorageKey(normalizedOwnerKey),
    )) || [];
  return items
    .map((conversation) => normalizeConversation(conversation, normalizedOwnerKey))
    .filter((conversation) => !normalizedOwnerKey || conversation.ownerKey === normalizedOwnerKey);
}

export async function listImageConversations(ownerKey = ""): Promise<ImageConversation[]> {
  return sortImageConversations(await readStoredImageConversations(ownerKey));
}

export async function saveImageConversations(conversations: ImageConversation[], ownerKey = ""): Promise<void> {
  await queueImageConversationWrite(async () => {
    const normalizedOwnerKey = normalizeOwnerKey(ownerKey);
    const items = await readStoredImageConversations(normalizedOwnerKey);
    const conversationMap = new Map(items.map((item) => [item.id, item]));
    for (const conversation of conversations.map((item) => normalizeConversation(item, normalizedOwnerKey))) {
      const current = conversationMap.get(conversation.id);
      conversationMap.set(conversation.id, current ? pickLatestConversation(current, conversation) : conversation);
    }
    await imageConversationStorage.setItem(
      getImageConversationsStorageKey(normalizedOwnerKey),
      sortImageConversations([...conversationMap.values()]),
    );
    emitImageConversationsChanged(normalizedOwnerKey);
  });
}

export async function saveImageConversation(conversation: ImageConversation, ownerKey = ""): Promise<void> {
  await queueImageConversationWrite(async () => {
    const normalizedOwnerKey = normalizeOwnerKey(ownerKey);
    const items = await readStoredImageConversations(normalizedOwnerKey);
    const nextConversation = normalizeConversation(conversation, normalizedOwnerKey);
    const current = items.find((item) => item.id === nextConversation.id);
    const persistedConversation = current ? pickLatestConversation(current, nextConversation) : nextConversation;
    const nextItems = sortImageConversations([
      persistedConversation,
      ...items.filter((item) => item.id !== persistedConversation.id),
    ]);
    await imageConversationStorage.setItem(getImageConversationsStorageKey(normalizedOwnerKey), nextItems);
    emitImageConversationsChanged(normalizedOwnerKey);
  });
}

export async function deleteImageConversation(id: string, ownerKey = ""): Promise<void> {
  await queueImageConversationWrite(async () => {
    const normalizedOwnerKey = normalizeOwnerKey(ownerKey);
    const items = await readStoredImageConversations(normalizedOwnerKey);
    await imageConversationStorage.setItem(
      getImageConversationsStorageKey(normalizedOwnerKey),
      items.filter((item) => item.id !== id),
    );
    emitImageConversationsChanged(normalizedOwnerKey);
  });
}

export async function clearImageConversations(ownerKey = ""): Promise<void> {
  await queueImageConversationWrite(async () => {
    const normalizedOwnerKey = normalizeOwnerKey(ownerKey);
    await imageConversationStorage.removeItem(getImageConversationsStorageKey(normalizedOwnerKey));
    emitImageConversationsChanged(normalizedOwnerKey);
  });
}

export function getImageConversationStats(conversation: ImageConversation | null): ImageConversationStats {
  if (!conversation) {
    return { queued: 0, running: 0 };
  }

  return conversation.turns.reduce(
    (acc, turn) => {
      if (turn.status === "queued") {
        acc.queued += 1;
      } else if (turn.status === "generating") {
        acc.running += 1;
      }
      return acc;
    },
    { queued: 0, running: 0 },
  );
}
