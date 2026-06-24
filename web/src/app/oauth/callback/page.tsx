"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { LoaderCircle } from "lucide-react";
import { toast } from "sonner";

import { getPostAuthRedirect } from "@/lib/use-auth-guard";
import { setStoredAuthSession, type AuthRole } from "@/store/auth";

function readFragmentParams() {
  if (typeof window === "undefined") {
    return new URLSearchParams();
  }
  const raw = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : window.location.hash;
  return new URLSearchParams(raw);
}

export default function OAuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    const params = readFragmentParams();
    const error = params.get("error");
    if (error) {
      toast.error(error);
      router.replace("/login");
      return;
    }

    const token = params.get("token") || "";
    const role = params.get("role") === "admin" ? "admin" : "user";
    if (!token) {
      toast.error("OAuth 登录未返回有效会话");
      router.replace("/login");
      return;
    }

    void setStoredAuthSession({
      key: token,
      role: role as AuthRole,
      subjectId: params.get("subject_id") || "",
      name: params.get("name") || "Linux DO",
      email: params.get("email") || undefined,
      quota: Number(params.get("quota") || 0),
    }).then(() => {
      router.replace(getPostAuthRedirect(role as AuthRole, { consume: true }));
    });
  }, [router]);

  return (
    <div className="grid min-h-[calc(100vh-1rem)] w-full place-items-center px-4 py-6">
      <LoaderCircle className="size-5 animate-spin text-rose-400" />
    </div>
  );
}
