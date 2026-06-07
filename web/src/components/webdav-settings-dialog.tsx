"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { ImageWebDAVConfig, ImageWebDAVConfigPayload } from "@/lib/api";

type WebDAVSettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  config: ImageWebDAVConfig | null;
  isSaving: boolean;
  title: string;
  description: string;
  onSave: (payload: ImageWebDAVConfigPayload) => Promise<void>;
};

export function WebDAVSettingsDialog({
  open,
  onOpenChange,
  config,
  isSaving,
  title,
  description,
  onSave,
}: WebDAVSettingsDialogProps) {
  const [enabled, setEnabled] = useState(false);
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rootPath, setRootPath] = useState("");

  useEffect(() => {
    if (!open) return;
    setEnabled(Boolean(config?.enabled));
    setUrl(config?.url || "");
    setUsername(config?.username || "");
    setPassword("");
    setRootPath(config?.root_path || "");
  }, [config, open]);

  const handleSave = async () => {
    await onSave({
      enabled,
      url: url.trim(),
      username: username.trim(),
      password,
      root_path: rootPath.trim(),
    });
    setPassword("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          <label className="flex items-center gap-3 rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700">
            <Checkbox checked={enabled} onCheckedChange={(checked) => setEnabled(checked === true)} />
            启用自动保存
          </label>
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-stone-700">WebDAV 地址</label>
            <Input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/remote.php/dav/files/name"
              className="h-10 rounded-lg border-stone-200 bg-white"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-stone-700">用户名</label>
              <Input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="h-10 rounded-lg border-stone-200 bg-white"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-stone-700">密码</label>
              <Input
                value={password}
                type="password"
                onChange={(event) => setPassword(event.target.value)}
                placeholder={config?.password_set ? "留空则保持原密码" : ""}
                className="h-10 rounded-lg border-stone-200 bg-white"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-stone-700">远程目录</label>
            <Input
              value={rootPath}
              onChange={(event) => setRootPath(event.target.value)}
              placeholder="YanAI"
              className="h-10 rounded-lg border-stone-200 bg-white"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSaving}>
            取消
          </Button>
          <Button onClick={() => void handleSave()} disabled={isSaving}>
            {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
