"use client";

import { useEffect, useState } from "react";
import { Gift, LoaderCircle, Save, Sparkles, TestTube, Waypoints } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchMe,
  fetchMyImageChannel,
  redeemMyCode,
  testMyImageChannelModels,
  updateMyImageChannel,
  updateMyProfile,
  type CurrentUser,
  type UserImageChannel,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const DEFAULT_CHANNEL_MODELS = "gpt-image-2,codex-gpt-image-2,gpt-5-5";

type ImageChannelForm = {
  enabled: boolean;
  name: string;
  base_url: string;
  api_key: string;
  models: string;
  timeout: string;
  hasApiKey: boolean;
};

const emptyImageChannelForm = (): ImageChannelForm => ({
  enabled: false,
  name: "个人生图渠道",
  base_url: "",
  api_key: "",
  models: DEFAULT_CHANNEL_MODELS,
  timeout: "60",
  hasApiKey: false,
});

const channelToForm = (channel?: UserImageChannel | null): ImageChannelForm => ({
  enabled: Boolean(channel?.enabled),
  name: channel?.name || "个人生图渠道",
  base_url: channel?.base_url || "",
  api_key: "",
  models: channel?.models?.join(",") || DEFAULT_CHANNEL_MODELS,
  timeout: String(channel?.timeout ?? 60),
  hasApiKey: Boolean(channel?.has_api_key),
});

const toNumber = (value: string, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const uniqueModels = (value: string) => {
  const seen = new Set<string>();
  return value
    .replace(/;/g, ",")
    .replace(/\n/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) return false;
      seen.add(item);
      return true;
    });
};

function ProfileContent() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [channelForm, setChannelForm] = useState<ImageChannelForm>(emptyImageChannelForm);
  const [channelTestMessage, setChannelTestMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isSavingChannel, setIsSavingChannel] = useState(false);
  const [isTestingChannel, setIsTestingChannel] = useState(false);

  const load = async () => {
    setIsLoading(true);
    try {
      const [me, imageChannel] = await Promise.all([fetchMe(), fetchMyImageChannel()]);
      setUser(me.user);
      setName(me.user.name || "");
      setChannelForm(channelToForm(imageChannel.channel));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载个人信息失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const data = await updateMyProfile({ name: name.trim() });
      setUser(data.user);
      toast.success("资料已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  const channelPayload = () => ({
    enabled: channelForm.enabled,
    name: channelForm.name.trim(),
    base_url: channelForm.base_url.trim(),
    api_key: channelForm.api_key.trim(),
    models: channelForm.models,
    timeout: toNumber(channelForm.timeout, 60),
  });

  const handleSaveChannel = async () => {
    setIsSavingChannel(true);
    try {
      const data = await updateMyImageChannel(channelPayload());
      setUser(data.user);
      setChannelForm(channelToForm(data.channel));
      setChannelTestMessage(null);
      toast.success("个人生图渠道已保存");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存个人生图渠道失败");
    } finally {
      setIsSavingChannel(false);
    }
  };

  const handleTestChannel = async () => {
    setIsTestingChannel(true);
    try {
      const result = await testMyImageChannelModels({
        ...channelPayload(),
        test_models: uniqueModels(channelForm.models),
      });
      const text = result.ok
        ? `${result.tested_models.length || result.model_count} 个模型可用 · ${result.latency_ms}ms`
        : result.error || "模型测试失败";
      setChannelTestMessage({ ok: result.ok, text });
      if (result.ok) {
        toast.success(text);
      } else {
        toast.error(text);
      }
    } catch (error) {
      const text = error instanceof Error ? error.message : "模型测试失败";
      setChannelTestMessage({ ok: false, text });
      toast.error(text);
    } finally {
      setIsTestingChannel(false);
    }
  };

  const handleRedeem = async () => {
    try {
      const data = await redeemMyCode(code.trim());
      setUser(data.user);
      setCode("");
      toast.success(`兑换成功，增加 ${data.redeem_code.quota} 点额度`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "兑换失败");
    }
  };

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-rose-400" />
      </div>
    );
  }

  return (
    <section className="mx-auto w-full max-w-5xl space-y-5">
      <div className="space-y-1">
        <div className="text-xs font-semibold tracking-[0.18em] text-rose-400 uppercase">Profile</div>
        <h1 className="text-2xl font-semibold tracking-tight">个人中心</h1>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="rounded-lg border-white/80 bg-white/80 shadow-sm md:col-span-2">
          <CardContent className="space-y-5 p-6">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-stone-500">当前账号</div>
                <div className="mt-1 text-lg font-semibold text-stone-950">{user?.email}</div>
              </div>
              <Badge variant={user?.status === "disabled" ? "secondary" : "success"}>
                {user?.status === "disabled" ? "已禁用" : "正常"}
              </Badge>
            </div>
            <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="昵称" className="h-11 rounded-xl border-rose-100 bg-white" />
              <Button className="h-11 rounded-xl bg-rose-500 text-white hover:bg-rose-600" onClick={() => void handleSave()} disabled={isSaving}>
                {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                保存资料
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-lg border-white/80 bg-white/80 shadow-sm">
          <CardContent className="space-y-2 p-6">
            <div className="rounded-2xl bg-rose-50 p-3 text-rose-500 w-fit">
              <Sparkles className="size-5" />
            </div>
            <div className="text-sm text-stone-500">可用额度</div>
            <div className="text-4xl font-semibold text-rose-600">{user?.quota ?? 0}</div>
            <div className="text-xs text-stone-400">已消耗 {user?.spent_quota ?? user?.quota_used ?? 0} 点</div>
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-lg border-white/80 bg-white/80 shadow-sm">
        <CardContent className="space-y-5 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-800">
              <Waypoints className="size-4 text-rose-500" />
              个人生图渠道
            </div>
            <label className="flex items-center gap-2 text-sm font-medium text-stone-700">
              <Checkbox
                checked={channelForm.enabled}
                onCheckedChange={(checked) => setChannelForm((current) => ({ ...current, enabled: checked === true }))}
              />
              启用
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1.5 text-xs font-semibold text-stone-700">
              <span>名称</span>
              <Input
                value={channelForm.name}
                onChange={(event) => setChannelForm((current) => ({ ...current, name: event.target.value }))}
                placeholder="个人生图渠道"
                className="h-10 rounded-xl border-rose-100 bg-white text-sm font-normal"
              />
            </label>
            <label className="space-y-1.5 text-xs font-semibold text-stone-700">
              <span>Base URL</span>
              <Input
                value={channelForm.base_url}
                onChange={(event) => setChannelForm((current) => ({ ...current, base_url: event.target.value }))}
                placeholder="https://api.example.com"
                className="h-10 rounded-xl border-rose-100 bg-white text-sm font-normal"
              />
            </label>
            <label className="space-y-1.5 text-xs font-semibold text-stone-700">
              <span>API Key</span>
              <Input
                type="password"
                value={channelForm.api_key}
                onChange={(event) => setChannelForm((current) => ({ ...current, api_key: event.target.value }))}
                placeholder={channelForm.hasApiKey ? "留空保留当前密钥" : "sk-..."}
                autoComplete="new-password"
                className="h-10 rounded-xl border-rose-100 bg-white text-sm font-normal"
              />
            </label>
            <label className="space-y-1.5 text-xs font-semibold text-stone-700">
              <span>超时秒数</span>
              <Input
                type="number"
                value={channelForm.timeout}
                onChange={(event) => setChannelForm((current) => ({ ...current, timeout: event.target.value }))}
                placeholder="60"
                className="h-10 rounded-xl border-rose-100 bg-white text-sm font-normal"
              />
            </label>
            <label className="space-y-1.5 text-xs font-semibold text-stone-700 md:col-span-2">
              <span>模型</span>
              <Textarea
                value={channelForm.models}
                onChange={(event) => setChannelForm((current) => ({ ...current, models: event.target.value }))}
                placeholder={DEFAULT_CHANNEL_MODELS}
                className="min-h-20 rounded-xl border-rose-100 bg-white text-sm font-normal"
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            {channelTestMessage ? (
              <div className={channelTestMessage.ok ? "text-xs font-medium text-emerald-700" : "text-xs font-medium text-rose-600"}>
                {channelTestMessage.text}
              </div>
            ) : (
              <div className="text-xs text-stone-400">
                {channelForm.hasApiKey ? "已保存密钥" : "未保存密钥"}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                variant="outline"
                className="h-10 rounded-xl border-rose-100 bg-white"
                disabled={isTestingChannel}
                onClick={() => void handleTestChannel()}
              >
                {isTestingChannel ? <LoaderCircle className="size-4 animate-spin" /> : <TestTube className="size-4" />}
                测试
              </Button>
              <Button
                className="h-10 rounded-xl bg-rose-500 text-white hover:bg-rose-600"
                disabled={isSavingChannel}
                onClick={() => void handleSaveChannel()}
              >
                {isSavingChannel ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                保存渠道
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="rounded-lg border-white/80 bg-white/80 shadow-sm">
        <CardContent className="space-y-4 p-6">
          <div className="flex items-center gap-2 text-sm font-semibold text-stone-800">
            <Gift className="size-4 text-rose-500" />
            兑换额度
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <Input value={code} onChange={(event) => setCode(event.target.value)} placeholder="输入兑换码" className="h-11 rounded-xl border-rose-100 bg-white uppercase" />
            <Button className="h-11 rounded-xl bg-rose-500 text-white hover:bg-rose-600" onClick={() => void handleRedeem()}>
              立即兑换
            </Button>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}

export default function ProfilePage() {
  const { isCheckingAuth, session } = useAuthGuard(["user"]);
  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-rose-400" />
      </div>
    );
  }
  return <ProfileContent />;
}
