import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "@/lib/api";

function readHashParams() {
  const raw = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  return new URLSearchParams(raw);
}

function readSearchParams() {
  return new URLSearchParams(window.location.search || "");
}

function mapGoogleError(error, errorDescription) {
  if (errorDescription) return errorDescription;
  const mapping = {
    google_access_denied: "Bạn đã hủy đăng nhập Google.",
    google_email_not_verified: "Email Google chưa được xác minh.",
    user_inactive: "Tài khoản đã bị khóa.",
    google_callback_invalid: "Phiên đăng nhập Google hết hạn. Vui lòng thử lại.",
  };
  return mapping[error] || "Đăng nhập Google thất bại.";
}

export default function GoogleAuthCallbackPage() {
  const navigate = useNavigate();
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    let active = true;
    let timeoutId = null;

    async function completeGoogleAuth() {
      const hashParams = readHashParams();
      const searchParams = readSearchParams();
      const accessToken = hashParams.get("access_token") || searchParams.get("access_token");
      const refreshToken = hashParams.get("refresh_token") || searchParams.get("refresh_token");
      const error = hashParams.get("error") || searchParams.get("error");
      const errorDescription =
        hashParams.get("error_description") || searchParams.get("error_description");

      if (accessToken && refreshToken) {
        api.saveTokenPair(accessToken, refreshToken);
        try {
          const user = await api.me();
          localStorage.setItem("current_user", JSON.stringify(user));
          window.history.replaceState(null, "", window.location.pathname);
          navigate(user.role === "admin" ? "/admin" : "/home", { replace: true });
          return;
        } catch {
          api.clearSession();
          timeoutId = window.setTimeout(() => navigate("/login", { replace: true }), 1200);
          return;
        }
      }

      if (error) {
        const loginMessage = mapGoogleError(error, errorDescription);
        if (active) {
          navigate(`/login?google_error=${encodeURIComponent(loginMessage)}`, { replace: true });
        }
        return;
      }

      if (active) {
        navigate(
          `/login?google_error=${encodeURIComponent("Thiếu thông tin phiên đăng nhập từ Google.")}`,
          { replace: true }
        );
      }
    }

    completeGoogleAuth();

    return () => {
      active = false;
      if (timeoutId) window.clearTimeout(timeoutId);
    };
  }, [navigate]);

  return null;
}
