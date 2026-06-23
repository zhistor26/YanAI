import { toast } from "sonner";

import {
  isUserCancelledSave,
  saveImageSourceToDisk,
  saveStoredImageToDisk,
} from "@/lib/lazycat-drive";
import type { StoredImage } from "@/store/image-conversations";

export async function saveStoredImageWithToast(image: StoredImage, fileName: string) {
  const toastId = toast.loading("正在准备图片…");
  try {
    const outcome = await saveStoredImageToDisk(image, fileName);
    toast.dismiss(toastId);
    if (outcome === "saved") {
      toast.success("保存成功");
    }
  } catch (error) {
    toast.dismiss(toastId);
    if (isUserCancelledSave(error)) {
      return;
    }
    toast.error(error instanceof Error ? error.message : "保存失败");
  }
}

export async function saveImageSourceWithToast(source: string, fileName: string, recordId = "") {
  const toastId = toast.loading("正在准备图片…");
  try {
    const outcome = await saveImageSourceToDisk(source, fileName, recordId);
    toast.dismiss(toastId);
    if (outcome === "saved") {
      toast.success("保存成功");
    }
  } catch (error) {
    toast.dismiss(toastId);
    if (isUserCancelledSave(error)) {
      return;
    }
    toast.error(error instanceof Error ? error.message : "保存失败");
  }
}
