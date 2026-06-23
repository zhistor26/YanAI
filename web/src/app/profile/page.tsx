"use client";

import { useEffect, useState } from "react";
import { Gift, LoaderCircle, Save, Sparkles, TestTube, Waypoints } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
  type DefaultImageChannel,
  type UserImageChannel,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const DEFAULT_CHANNEL_MODELS = "gpt-image-2";

type ChannelSource = "default" | "personal";

type ImageChannelForm = {
  source: ChannelSource;
  name: string;
  api_type: "openai_image" | "async_videos";
  base_url: string;
  api_key: string;
  models: string;
  timeout: string;
  hasApiKey: boolean;
};

const emptyImageChannelForm = (): ImageChannelForm => ({
  source: "default",
  name: "个人生图渠道",
  api_type: "async_videos",
  base_url: "",
  api_key: "",
  models: DEFAULT_CHANNEL_MODELS,
  timeout: "180",
  hasApiKey: false,
});

const channelToForm = (channel?: UserImageChannel | null): ImageChannelForm => ({
  source: channel?.source === "personal" || channel?.enabled ? "personal" : "default",
  name: channel?.name || "个人生图渠道",
  api_type: channel?.type === "openai_image" ? "openai_image" : "async_videos",
  base_url: channel?.base_url || "",
  api_key: "",
  models: channel?.models?.join(",") || DEFAULT_CHANNEL_MODELS,
  timeout: String(channel?.timeout ?? 180),
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

const channelTypeLabel = (type?: string) => {
  if (type === "async_videos") {
    return "gpt-image-2 异步（POST /v1/videos）";
  }
  if (type === "openai_image") {
    return "OpenAI 兼容（POST /v1/images/generations）";
  }
  return type || "未知类型";
};

function DefaultChannelCard({ channel }: { channel: DefaultImageChannel }) {
  return (
    <div className="rounded-xl border border-rose-100 bg-rose-50/40 p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-stone-900">{channel.name || "默认渠道"}</div>
        <Badge variant="success">系统内置</Badge>
      </div>
      <div className="grid gap-2 text-sm text-stone-600 md:grid-cols-2">
        <div>
          <span className="text-stone-400">接口类型 · </span>
          {channelTypeLabel(channel.type)}
        </div>
        <div>
          <span className="text-stone-400">Base URL · </span>
          {channel.base_url || "-"}
        </div>
        <div>
          <span className="text-stone-400">超时 · </span>
          {channel.timeout ?? 180} 秒
        </div>
        <div>
          <span className="text-stone-400">API Key · </span>
          {channel.has_api_key ? "已配置" : "未配置"}
        </div>
      </div>
      <div className="text-sm text-stone-600">
        <span className="text-stone-400">模型 · </span>
        {(channel.models || []).join(", ") || DEFAULT_CHANNEL_MODELS}
      </div>
    </div>
  );
}

function ProfileContent() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [channelForm, setChannelForm] = useState<ImageChannelForm>(emptyImageChannelForm);
  const [defaultChannels, setDefaultChannels] = useState<DefaultImageChannel[]>([]);
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
      setDefaultChannels(imageChannel.default_channels || []);
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
    source: channelForm.source,
    enabled: channelForm.source === "personal",
    name: channelForm.name.trim(),
    type: channelForm.api_type,
    base_url: channelForm.base_url.trim(),
    api_key: channelForm.api_key.trim(),
    models: channelForm.models,
    timeout: toNumber(channelForm.timeout, 180),
  });

  const handleSaveChannel = async () => {
    setIsSavingChannel(true);
    try {
      const data = await updateMyImageChannel(channelPayload());
      setUser(data.user);
      setChannelForm(channelToForm(data.channel));
      setChannelTestMessage(null);
      toast.success(channelForm.source === "personal" ? "已切换为个人渠道" : "已切换为默认渠道");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存生图渠道失败");
    } finally {
      setIsSavingChannel(false);
    }
  };

  const handleTestChannel = async () => {
    setIsTestingChannel(true);
    try {
      const result = await testMyImageChannelModels({
        ...channelPayload(),
        source: "personal",
        enabled: true,
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
              生图渠道
            </div>
            <Badge variant={channelForm.source === "personal" ? "default" : "success"}>
              当前：{channelForm.source === "personal" ? "个人渠道" : "默认渠道"}
            </Badge>
          </div>

          <div className="grid grid-cols-2 gap-2 rounded-xl bg-stone-100 p-1">
            <button
              type="button"
              className={
                channelForm.source === "default"
                  ? "rounded-lg bg-white px-4 py-2 text-sm font-semibold text-stone-900 shadow-sm"
                  : "rounded-lg px-4 py-2 text-sm font-medium text-stone-500"
              }
              onClick={() => setChannelForm((current) => ({ ...current, source: "default" }))}
            >
              默认渠道
            </button>
            <button
              type="button"
              className={
                channelForm.source === "personal"
                  ? "rounded-lg bg-white px-4 py-2 text-sm font-semibold text-stone-900 shadow-sm"
                  : "rounded-lg px-4 py-2 text-sm font-medium text-stone-500"
              }
              onClick={() => setChannelForm((current) => ({ ...current, source: "personal" }))}
            >
              个人渠道
            </button>
          </div>

          {channelForm.source === "default" ? (
            <div className="space-y-3">
              <div className="text-sm text-stone-500">
                使用应用内置默认渠道生图，无需填写 API Key。管理员可在「渠道管理」修改默认配置。
              </div>
              {defaultChannels.length > 0 ? (
                defaultChannels.map((channel) => <DefaultChannelCard key={channel.id || channel.name} channel={channel} />)
              ) : (
                <DefaultChannelCard
                  channel={{
                    name: "otuapi",
                    type: "async_videos",
                    base_url: "https://otuapi.com",
                    models: [DEFAULT_CHANNEL_MODELS],
                    timeout: 180,
                    has_api_key: true,
                  }}
                />
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div className="text-sm text-stone-500">填写你自己的生图 API，保存后将优先使用个人渠道。</div>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="space-y-1.5 text-xs font-semibold text-stone-700 md:col-span-2">
                  <span>接口类型</span>
                  <select
                    value={channelForm.api_type}
                    onChange={(event) =>
                      setChannelForm((current) => ({
                        ...current,
                        api_type: event.target.value === "openai_image" ? "openai_image" : "async_videos",
                      }))
                    }
                    className="h-10 w-full rounded-xl border border-rose-100 bg-white px-3 text-sm font-normal text-stone-800"
                  >
                    <option value="async_videos">gpt-image-2 异步（POST /v1/videos，如 otuapi）</option>
                    <option value="openai_image">OpenAI 兼容（POST /v1/images/generations）</option>
                  </select>
                </label>
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
                    placeholder="https://otuapi.com"
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
                    placeholder="180"
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
                <Button
                  variant="outline"
                  className="h-10 rounded-xl border-rose-100 bg-white"
                  disabled={isTestingChannel}
                  onClick={() => void handleTestChannel()}
                >
                  {isTestingChannel ? <LoaderCircle className="size-4 animate-spin" /> : <TestTube className="size-4" />}
                  测试
                </Button>
              </div>
            </div>
          )}

          <div className="flex justify-end">
            <Button
              className="h-10 rounded-xl bg-rose-500 text-white hover:bg-rose-600"
              disabled={isSavingChannel}
              onClick={() => void handleSaveChannel()}
            >
              {isSavingChannel ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
              保存渠道设置
            </Button>
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
  const { isCheckingAuth, session } = useAuthGuard(["user", "admin"]);
  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-rose-400" />
      </div>
    );
  }
  return <ProfileContent />;
}
