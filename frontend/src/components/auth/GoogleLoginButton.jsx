import { useState } from "react";

import { api } from "@/lib/api";

function getButtonLabel(mode) {
  if (mode === "signup_with") return "Đăng ký bằng Gmail";
  return "Đăng nhập bằng Gmail";
}

export function GoogleLoginButton({ text = "signin_with", onError }) {
  const [loading, setLoading] = useState(false);
  const apiBase = import.meta.env.VITE_API_BASE_URL || "https://triumphant-charisma-production-f2a9.up.railway.app/api/v1";

  async function handleClick() {
    setLoading(true);
    try {
      const response = await fetch(`${apiBase}/auth/google/config`);
      const config = await response.json();

      if (!config?.redirect_enabled) {
        onError?.("Backend chưa cấu hình đầy đủ Google OAuth. Cần GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET và authlib.");
        return;
      }

      api.startGoogleLogin();
    } catch {
      onError?.("Không kết nối được backend để bắt đầu đăng nhập Gmail.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={loading}
      className="flex h-14 w-full items-center justify-center gap-3 rounded-2xl border border-zinc-200 bg-white px-4 text-base font-semibold text-zinc-900 transition hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-70"
    >
      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-[17px] font-bold text-[#4285F4]">
        G
      </span>
      {loading ? "Đang chuyển đến Gmail..." : getButtonLabel(text)}
    </button>
  );
}
