import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import {
  User,
  Mail,
  Lock,
  Eye,
  EyeOff,
  Save,
  LogOut,
  ShieldCheck,
  FileText,
  MessageSquare,
  Sparkles,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

function formatDate(value) {
  if (!value) return "Chưa có";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Chưa có";
  return date.toLocaleDateString("vi-VN");
}

export default function ProfilePage() {
  const navigate = useNavigate();
  const [profile, setProfile] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [fullName, setFullName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [profileMessage, setProfileMessage] = useState("");
  const [passwordMessage, setPasswordMessage] = useState("");
  const [loadingProfile, setLoadingProfile] = useState(false);
  const [loadingPassword, setLoadingPassword] = useState(false);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [me, meDashboard] = await Promise.all([api.me(), api.userDashboard()]);
        if (!active) return;
        setProfile(me);
        setDashboard(meDashboard);
        setFullName(me.full_name || "");
        localStorage.setItem("current_user", JSON.stringify(me));
      } catch (error) {
        if (!active) return;
        setProfileMessage(error.message || "Không tải được thông tin tài khoản.");
      }
    }

    load();

    return () => {
      active = false;
    };
  }, []);

  function handleLogout() {
    api.clearSession();
    navigate("/login");
  }

  async function handleSaveProfile() {
    setProfileMessage("");
    setLoadingProfile(true);
    try {
      const updated = await api.updateProfile({ full_name: fullName });
      setProfile(updated);
      localStorage.setItem("current_user", JSON.stringify(updated));
      setProfileMessage("Đã cập nhật hồ sơ.");
    } catch (error) {
      setProfileMessage(error.message || "Không cập nhật được hồ sơ.");
    } finally {
      setLoadingProfile(false);
    }
  }

  async function handleChangePassword() {
    setPasswordMessage("");
    if (!currentPassword || !newPassword || !confirmPassword) {
      setPasswordMessage("Cần nhập đủ thông tin mật khẩu.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordMessage("Mật khẩu xác nhận không khớp.");
      return;
    }

    setLoadingPassword(true);
    try {
      const response = await api.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPasswordMessage(response.detail || "Đã cập nhật mật khẩu.");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (error) {
      setPasswordMessage(error.message || "Không đổi được mật khẩu.");
    } finally {
      setLoadingPassword(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
          <div>
            <h1 className="text-xl font-semibold">Thông tin cá nhân</h1>
            <p className="text-sm text-slate-500">Dữ liệu được lấy trực tiếp từ backend người dùng.</p>
          </div>
          <Button variant="outline" onClick={handleLogout}>
            <LogOut className="mr-2 h-4 w-4" />
            Đăng xuất
          </Button>
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-6 py-8 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="grid gap-6 xl:grid-cols-[0.8fr_1.2fr]"
        >
          <div className="space-y-6">
            <Card className="rounded-[32px] border-0 shadow-sm">
              <CardContent className="p-8 text-center">
                <div className="mx-auto flex h-24 w-24 items-center justify-center rounded-full bg-slate-900 text-white">
                  <User className="h-10 w-10" />
                </div>
                <h2 className="mt-4 text-2xl font-semibold">{profile?.full_name || profile?.email || "Người dùng"}</h2>
                <p className="mt-1 text-sm text-slate-500">{profile?.email || "Chưa có email"}</p>
                <Badge className="mt-4 rounded-full">{profile?.role || "user"}</Badge>

                <div className="mt-6 grid gap-3 text-left">
                  {[
                    ["Tài liệu đã tải", dashboard?.total_documents ?? 0, FileText],
                    ["Lượt hỏi đáp", dashboard?.total_qa ?? 0, MessageSquare],
                    ["Bản tóm tắt", dashboard?.total_summaries ?? 0, Sparkles],
                  ].map(([label, value, Icon]) => (
                    <div
                      key={label}
                      className="flex items-center justify-between rounded-2xl bg-slate-50 p-4"
                    >
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-slate-700" />
                        <span className="text-sm text-slate-600">{label}</span>
                      </div>
                      <span className="font-semibold">{value}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card className="rounded-[32px] border-0 shadow-sm">
              <CardHeader>
                <CardTitle>Trạng thái tài khoản</CardTitle>
                <CardDescription>Thông tin nền của tài khoản hiện tại</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="rounded-2xl bg-slate-50 p-4">
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-slate-700" />
                    <p className="font-medium">Phương thức đăng nhập</p>
                  </div>
                  <p className="mt-2 text-sm text-slate-500">{profile?.auth_provider || "local"}</p>
                </div>

                <div className="rounded-2xl bg-slate-50 p-4">
                  <p className="font-medium">Ngày tham gia</p>
                  <p className="mt-2 text-sm text-slate-500">{formatDate(profile?.created_at)}</p>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="space-y-6">
            <Card className="rounded-[32px] border-0 shadow-sm">
              <CardHeader>
                <CardTitle>Chỉnh sửa hồ sơ</CardTitle>
                <CardDescription>Cập nhật các trường backend hiện đang hỗ trợ</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Họ và tên</label>
                  <div className="relative">
                    <User className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                    <Input
                      value={fullName}
                      onChange={(event) => setFullName(event.target.value)}
                      className="rounded-2xl pl-10"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">Email</label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                    <Input
                      value={profile?.email || ""}
                      className="rounded-2xl pl-10"
                      readOnly
                    />
                  </div>
                </div>

                {profileMessage ? (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                    {profileMessage}
                  </div>
                ) : null}

                <Button onClick={handleSaveProfile} disabled={loadingProfile} className="rounded-2xl">
                  <Save className="mr-2 h-4 w-4" />
                  {loadingProfile ? "Đang lưu..." : "Lưu thay đổi"}
                </Button>
              </CardContent>
            </Card>

            <Card className="rounded-[32px] border-0 shadow-sm">
              <CardHeader>
                <CardTitle>Đổi mật khẩu</CardTitle>
                <CardDescription>Cập nhật mật khẩu qua backend người dùng</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Mật khẩu hiện tại</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                    <Input
                      type="password"
                      value={currentPassword}
                      onChange={(event) => setCurrentPassword(event.target.value)}
                      placeholder="Nhập mật khẩu hiện tại"
                      className="rounded-2xl pl-10"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">Mật khẩu mới</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                    <Input
                      type={showPassword ? "text" : "password"}
                      value={newPassword}
                      onChange={(event) => setNewPassword(event.target.value)}
                      placeholder="Nhập mật khẩu mới"
                      className="rounded-2xl pl-10 pr-12"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((value) => !value)}
                      className="absolute right-3 top-3 text-slate-400 hover:text-slate-600"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">Xác nhận mật khẩu mới</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                    <Input
                      type="password"
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      placeholder="Nhập lại mật khẩu mới"
                      className="rounded-2xl pl-10"
                    />
                  </div>
                </div>

                {passwordMessage ? (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                    {passwordMessage}
                  </div>
                ) : null}

                <Button variant="outline" onClick={handleChangePassword} disabled={loadingPassword} className="rounded-2xl">
                  {loadingPassword ? "Đang cập nhật..." : "Cập nhật mật khẩu"}
                </Button>
              </CardContent>
            </Card>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
