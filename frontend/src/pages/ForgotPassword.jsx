import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [verified, setVerified] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function handleSendCode() {
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const res = await api.sendForgotPasswordCode(email);
      setSent(true);
      setMessage(res?.message || "Đã gửi mã xác nhận.");
    } catch (err) {
      setError(err.message || "Không gửi được mã xác nhận");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyCode() {
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const res = await api.verifyForgotPasswordCode(email, code);
      setVerified(true);
      setMessage(res?.message || "Xác nhận mã thành công.");
    } catch (err) {
      setError(err.message || "Mã xác nhận không hợp lệ");
    } finally {
      setLoading(false);
    }
  }

  async function handleResetPassword() {
    setError("");
    setMessage("");
    if (newPassword !== confirmPassword) {
      setError("Mật khẩu xác nhận không khớp");
      return;
    }
    setLoading(true);
    try {
      const res = await api.resetPasswordWithCode(email, code, newPassword);
      setMessage(res?.message || "Đổi mật khẩu thành công.");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError(err.message || "Không đổi được mật khẩu");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#f4f5ff] px-6 py-10 text-zinc-950">
      <div className="absolute inset-0 bg-[radial-gradient(130%_95%_at_50%_52%,rgba(255,255,255,0.86),rgba(231,231,255,0.88)_52%,rgba(211,213,252,0.95)_100%)]" />

      <div className="relative z-10 w-full max-w-md rounded-[28px] border border-white/65 bg-white/92 p-8 shadow-[0_24px_80px_rgba(76,81,132,0.16)] backdrop-blur">
        <div className="text-center">
          <p className="text-4xl font-black tracking-tight text-zinc-950 sm:text-5xl">PaperMind</p>
          <p className="mt-2 text-sm font-semibold uppercase tracking-[0.24em] text-indigo-600">Quên mật khẩu</p>
        </div>

        <div className="mt-8 space-y-5">
          <div className="space-y-2">
            <label className="text-sm font-medium text-zinc-700">Email đăng ký</label>
            <div className="relative">
              <Mail className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
              <Input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="Nhập email"
                className="h-14 rounded-2xl border-zinc-200 bg-zinc-50 pl-11 text-base text-zinc-950 placeholder:text-zinc-400 focus:border-indigo-400"
              />
            </div>
          </div>

          <Button
            disabled={loading || !email}
            onClick={handleSendCode}
            className="h-14 w-full rounded-2xl bg-indigo-600 text-base font-semibold text-white hover:bg-indigo-500"
          >
            {loading ? "Đang gửi mã..." : "Gửi mã 6 số"}
          </Button>

          {sent ? (
            <>
              <div className="space-y-2">
                <label className="text-sm font-medium text-zinc-700">Mã xác nhận</label>
                <div className="relative">
                  <ShieldCheck className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
                  <Input
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    placeholder="Nhập mã 6 số"
                    className="h-14 rounded-2xl border-zinc-200 bg-zinc-50 pl-11 text-base text-zinc-950 placeholder:text-zinc-400 focus:border-indigo-400"
                  />
                </div>
              </div>

              <Button
                disabled={loading || !code}
                onClick={handleVerifyCode}
                className="h-14 w-full rounded-2xl bg-emerald-600 text-base font-semibold text-white hover:bg-emerald-500"
              >
                {loading ? "Đang xác nhận..." : "Xác nhận mã"}
              </Button>

              {verified ? (
                <>
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-zinc-700">Mật khẩu mới</label>
                    <div className="relative">
                      <Lock className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
                      <Input
                        type={showPassword ? "text" : "password"}
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                        placeholder="Nhập mật khẩu mới"
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

                  <div className="space-y-2">
                    <label className="text-sm font-medium text-zinc-700">Xác nhận mật khẩu mới</label>
                    <div className="relative">
                      <Lock className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
                      <Input
                        type={showPassword ? "text" : "password"}
                        value={confirmPassword}
                        onChange={(event) => setConfirmPassword(event.target.value)}
                        placeholder="Nhập lại mật khẩu mới"
                        className="h-14 rounded-2xl border-zinc-200 bg-zinc-50 pl-11 text-base text-zinc-950 placeholder:text-zinc-400 focus:border-indigo-400"
                      />
                    </div>
                  </div>

                  <Button
                    disabled={loading || !newPassword || !confirmPassword}
                    onClick={handleResetPassword}
                    className="h-14 w-full rounded-2xl bg-indigo-600 text-base font-semibold text-white hover:bg-indigo-500"
                  >
                    {loading ? "Đang đổi mật khẩu..." : "Đổi mật khẩu"}
                  </Button>
                </>
              ) : null}
            </>
          ) : null}

          {message ? <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div> : null}
          {error ? <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div> : null}

          <p className="text-center text-sm text-zinc-500">
            <Link to="/login" className="font-semibold text-indigo-600 hover:text-indigo-500">
              Quay về đăng nhập
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
