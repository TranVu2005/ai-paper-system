import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Eye, EyeOff, Lock, Mail } from "lucide-react";

import { GoogleLoginButton } from "@/components/auth/GoogleLoginButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export default function LoginPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("user@example.com");
  const [password, setPassword] = useState("123456");
  const initialGoogleError = searchParams.get("google_error") || "";
  const [error, setError] = useState(initialGoogleError);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  React.useEffect(() => {
    if (!initialGoogleError) return;
    const next = new URLSearchParams(searchParams);
    next.delete("google_error");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await api.login(email, password);
      navigate(data.user?.role === "admin" ? "/admin" : "/home");
    } catch (err) {
      setError(err.message || "Đăng nhập thất bại");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#f4f5ff] px-6 py-10 text-zinc-950">
      <div className="absolute inset-0 bg-[radial-gradient(130%_95%_at_50%_52%,rgba(255,255,255,0.86),rgba(231,231,255,0.88)_52%,rgba(211,213,252,0.95)_100%)]" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_9%_18%,rgba(164,177,255,0.26),transparent_28%),radial-gradient(circle_at_90%_16%,rgba(182,168,255,0.2),transparent_24%),radial-gradient(circle_at_84%_84%,rgba(153,185,255,0.22),transparent_29%),radial-gradient(circle_at_14%_84%,rgba(175,170,255,0.22),transparent_30%)]" />
      <div className="absolute inset-0 bg-[linear-gradient(rgba(160,171,220,0.16)_1px,transparent_1px),linear-gradient(90deg,rgba(160,171,220,0.16)_1px,transparent_1px)] bg-[size:56px_56px] opacity-25" />
      <div className="absolute -left-14 -top-10 h-64 w-64 rotate-[-22deg] rounded-[36px] border border-indigo-200/45 bg-white/38 shadow-[0_20px_55px_rgba(99,102,241,0.12)] backdrop-blur-[2px]" />
      <div className="absolute -left-2 top-8 h-52 w-44 rotate-[-12deg] rounded-[30px] border border-indigo-200/35 bg-white/30 shadow-[0_20px_55px_rgba(99,102,241,0.08)] backdrop-blur-[2px]" />
      <div className="absolute -right-10 top-16 h-56 w-40 rotate-[16deg] rounded-[30px] border border-violet-200/45 bg-white/35 shadow-[0_20px_55px_rgba(124,58,237,0.1)] backdrop-blur-[2px]" />
      <div className="absolute right-2 top-24 h-44 w-36 rotate-[9deg] rounded-[24px] border border-indigo-200/35 bg-white/28 shadow-[0_16px_44px_rgba(99,102,241,0.08)] backdrop-blur-[2px]" />
      <div className="absolute -bottom-10 -right-12 h-60 w-60 rounded-full border border-sky-200/55 bg-white/26 blur-3xl" />
      <div className="absolute -bottom-14 -left-14 h-56 w-56 rounded-full border border-indigo-200/55 bg-white/26 blur-3xl" />
      <div className="absolute -top-20 left-[-8%] h-60 w-[42%] rotate-[-8deg] rounded-[999px] border border-white/45 bg-[linear-gradient(90deg,rgba(255,255,255,0.36),rgba(194,201,255,0.08))] opacity-75 blur-[1px]" />
      <div className="absolute -bottom-24 right-[-6%] h-72 w-[46%] rotate-[10deg] rounded-[999px] border border-white/40 bg-[linear-gradient(90deg,rgba(176,194,255,0.12),rgba(255,255,255,0.28))] opacity-75 blur-[1px]" />
      <div className="absolute left-[8%] top-[24%] h-36 w-36 rounded-full border border-indigo-200/35" />
      <div className="absolute right-[10%] bottom-[21%] h-44 w-44 rounded-full border border-sky-200/35" />
      <div className="absolute left-[6%] top-[32%] h-16 w-16 rounded-full border border-indigo-200/40" />
      <div className="absolute right-[7%] top-[27%] h-20 w-20 rounded-full border border-violet-200/40" />
      <div className="absolute bottom-[12%] left-[24%] h-px w-28 rotate-[8deg] bg-indigo-300/45" />
      <div className="absolute bottom-[14%] left-[29%] h-px w-20 rotate-[-14deg] bg-indigo-300/45" />
      <div className="absolute left-[15%] top-[16%] h-2.5 w-2.5 rounded-full bg-indigo-300/70 shadow-[0_0_14px_rgba(129,140,248,0.75)]" />
      <div className="absolute right-[21%] top-[11%] h-2 w-2 rounded-full bg-violet-300/65 shadow-[0_0_12px_rgba(167,139,250,0.65)]" />
      <div className="absolute bottom-[18%] right-[29%] h-2.5 w-2.5 rounded-full bg-sky-300/65 shadow-[0_0_12px_rgba(125,211,252,0.65)]" />
      <div className="absolute left-[18%] top-[17%] h-px w-16 bg-indigo-200/55" />
      <div className="absolute left-[34%] top-[17%] h-1.5 w-1.5 rounded-full bg-indigo-200/70" />
      <div className="absolute left-[34.4%] top-[17.4%] h-10 w-px rotate-[34deg] bg-indigo-200/45 origin-top" />
      <div className="absolute left-[36.6%] top-[21.2%] h-1.5 w-1.5 rounded-full bg-indigo-200/70" />
      <svg
        viewBox="0 0 360 220"
        className="absolute left-0 top-0 h-[34%] w-[34%] opacity-45"
        aria-hidden="true"
      >
        <polygon points="42,58 132,30 188,82 126,148 52,122" fill="rgba(255,255,255,0.16)" />
        <line x1="42" y1="58" x2="132" y2="30" stroke="rgba(165,180,252,0.42)" />
        <line x1="132" y1="30" x2="188" y2="82" stroke="rgba(165,180,252,0.42)" />
        <line x1="188" y1="82" x2="126" y2="148" stroke="rgba(165,180,252,0.42)" />
        <line x1="126" y1="148" x2="52" y2="122" stroke="rgba(165,180,252,0.42)" />
        <line x1="52" y1="122" x2="42" y2="58" stroke="rgba(165,180,252,0.42)" />
        <line x1="42" y1="58" x2="188" y2="82" stroke="rgba(199,210,254,0.36)" />
        <line x1="132" y1="30" x2="126" y2="148" stroke="rgba(199,210,254,0.36)" />
        <circle cx="42" cy="58" r="4" fill="rgba(255,255,255,0.82)" />
        <circle cx="132" cy="30" r="4.5" fill="rgba(199,210,254,0.9)" />
        <circle cx="188" cy="82" r="4" fill="rgba(255,255,255,0.82)" />
        <circle cx="126" cy="148" r="4" fill="rgba(199,210,254,0.9)" />
        <circle cx="52" cy="122" r="3.5" fill="rgba(255,255,255,0.74)" />
      </svg>
      <svg
        viewBox="0 0 420 240"
        className="absolute bottom-0 right-0 h-[36%] w-[38%] opacity-50"
        aria-hidden="true"
      >
        <polygon points="250,76 352,42 398,124 336,206 236,178" fill="rgba(255,255,255,0.12)" />
        <line x1="250" y1="76" x2="352" y2="42" stroke="rgba(125,211,252,0.4)" />
        <line x1="352" y1="42" x2="398" y2="124" stroke="rgba(125,211,252,0.4)" />
        <line x1="398" y1="124" x2="336" y2="206" stroke="rgba(125,211,252,0.4)" />
        <line x1="336" y1="206" x2="236" y2="178" stroke="rgba(125,211,252,0.4)" />
        <line x1="236" y1="178" x2="250" y2="76" stroke="rgba(125,211,252,0.4)" />
        <line x1="250" y1="76" x2="336" y2="206" stroke="rgba(186,230,253,0.36)" />
        <line x1="352" y1="42" x2="236" y2="178" stroke="rgba(186,230,253,0.36)" />
        <line x1="398" y1="124" x2="250" y2="76" stroke="rgba(186,230,253,0.3)" />
        <circle cx="250" cy="76" r="4" fill="rgba(240,249,255,0.88)" />
        <circle cx="352" cy="42" r="4.5" fill="rgba(186,230,253,0.9)" />
        <circle cx="398" cy="124" r="4" fill="rgba(240,249,255,0.88)" />
        <circle cx="336" cy="206" r="4" fill="rgba(186,230,253,0.9)" />
        <circle cx="236" cy="178" r="3.5" fill="rgba(240,249,255,0.8)" />
      </svg>

      <div className="relative z-10 w-full max-w-md rounded-[28px] border border-white/65 bg-white/90 p-8 shadow-[0_24px_80px_rgba(76,81,132,0.16)] backdrop-blur">
        <div className="text-center">
          <p className="text-4xl font-black tracking-tight text-zinc-950 sm:text-5xl">PaperMind</p>
          <p className="mt-2 text-sm font-semibold uppercase tracking-[0.24em] text-indigo-600">
            Đăng nhập
          </p>
        </div>

        <form onSubmit={handleSubmit} className="mt-8 space-y-5">
          <div className="space-y-2">
            <label className="text-sm font-medium text-zinc-700">Email</label>
            <div className="relative">
              <Mail className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
              <Input
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="Địa chỉ email"
                className="h-14 rounded-2xl border-zinc-200 bg-zinc-50 pl-11 text-base text-zinc-950 placeholder:text-zinc-400 focus:border-indigo-400"
              />
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-zinc-700">Mật khẩu</label>
              <Link to="/forgot-password" className="text-sm font-medium text-indigo-600 hover:text-indigo-500">
                Quên mật khẩu?
              </Link>
            </div>
            <div className="relative">
              <Lock className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
              <Input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Nhập mật khẩu"
                className="h-14 rounded-2xl border-zinc-200 bg-zinc-50 pl-11 pr-12 text-base text-zinc-950 placeholder:text-zinc-400 focus:border-indigo-400"
              />
              <button
                type="button"
                onClick={() => setShowPassword((value) => !value)}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-zinc-400 transition hover:text-zinc-600"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          {error ? (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
              {error}
            </div>
          ) : null}

          <Button
            disabled={loading}
            className="h-14 w-full rounded-2xl bg-indigo-600 text-base font-semibold text-white hover:bg-indigo-500"
          >
            {loading ? "Đang đăng nhập..." : "Đăng nhập"}
          </Button>
        </form>

        <div className="my-6 flex items-center gap-3 text-xs uppercase tracking-[0.24em] text-zinc-400">
          <div className="h-px flex-1 bg-zinc-200" />
          Hoặc
          <div className="h-px flex-1 bg-zinc-200" />
        </div>

        <GoogleLoginButton
          text="signin_with"
          onError={(message) => setError(message || "Đăng nhập Gmail thất bại")}
        />

        <p className="mt-6 text-center text-sm text-zinc-500">
          Chưa có tài khoản?{" "}
          <Link to="/register" className="font-semibold text-indigo-600 hover:text-indigo-500">
            Đăng ký ngay
          </Link>
        </p>
      </div>
    </div>
  );
}
