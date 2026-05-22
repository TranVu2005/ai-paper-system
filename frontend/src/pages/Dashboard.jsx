import React, { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  Shield,
  Users,
  FileText,
  BarChart3,
  Settings,
  Bell,
  Search,
  UserCog,
  FolderOpen,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Database,
  Lock,
  Eye,
  Trash2,
  Ban,
  Filter,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";

const users = [
  {
    id: 1,
    name: "Nguyễn Văn A",
    email: "vana@example.com",
    role: "Member",
    status: "Hoạt động",
    uploads: 12,
    lastActive: "10 phút trước",
  },
  {
    id: 2,
    name: "Trần Thị B",
    email: "thib@example.com",
    role: "Editor",
    status: "Hoạt động",
    uploads: 29,
    lastActive: "1 giờ trước",
  },
  {
    id: 3,
    name: "Lê Văn C",
    email: "vanc@example.com",
    role: "Member",
    status: "Tạm khóa",
    uploads: 7,
    lastActive: "Hôm qua",
  },
  {
    id: 4,
    name: "Phạm Thị D",
    email: "thid@example.com",
    role: "Moderator",
    status: "Hoạt động",
    uploads: 21,
    lastActive: "5 phút trước",
  },
];

const documents = [
  {
    id: 1,
    title: "Khai phá dữ liệu trong phân tích y sinh học",
    owner: "Nguyễn Văn A",
    pages: 18,
    status: "Đã duyệt",
    flag: "Bình thường",
    updated: "2 giờ trước",
  },
  {
    id: 2,
    title: "Mô hình RAG cho tóm tắt văn bản tiếng Việt",
    owner: "Trần Thị B",
    pages: 26,
    status: "Chờ duyệt",
    flag: "Chưa kiểm duyệt",
    updated: "15 phút trước",
  },
  {
    id: 3,
    title: "Ứng dụng vector database trong học thuật",
    owner: "Lê Văn C",
    pages: 31,
    status: "Bị gắn cờ",
    flag: "Nghi trùng lặp",
    updated: "Hôm qua",
  },
  {
    id: 4,
    title: "Hệ thống gợi ý tài liệu khoa học",
    owner: "Phạm Thị D",
    pages: 20,
    status: "Đã duyệt",
    flag: "Bình thường",
    updated: "3 ngày trước",
  },
];

const logs = [
  "Admin đã duyệt 5 tài liệu mới trong hôm nay.",
  "1 tài khoản bị khóa do vi phạm nội dung tải lên.",
  "Vector database đang được đồng bộ lại 89%.",
  "Hệ thống phát hiện 2 tài liệu nghi trùng lặp.",
];

const navItems = [
  { key: "overview", label: "Tổng quan", icon: Shield },
  { key: "users", label: "Quản lý người dùng", icon: Users },
  { key: "documents", label: "Quản lý tài liệu", icon: FolderOpen },
  { key: "moderation", label: "Kiểm duyệt", icon: CheckCircle2 },
  { key: "analytics", label: "Thống kê hệ thống", icon: BarChart3 },
  { key: "system", label: "Hệ thống", icon: Database },
  { key: "settings", label: "Cấu hình", icon: Settings },
];

function StatCard({ title, value, sub, icon }) {
  return (
    <Card className="rounded-2xl border-0 shadow-sm">
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-slate-500">{title}</p>
            <h3 className="mt-2 text-2xl font-semibold text-slate-900">
              {value}
            </h3>
            <p className="mt-1 text-sm text-slate-500">{sub}</p>
          </div>
          <div className="rounded-2xl bg-slate-100 p-3">
            {React.createElement(icon, { className: "h-5 w-5 text-slate-700" })}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function AdminDashboard() {
  const [active, setActive] = useState("overview");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("pending");
  const [adminUsers, setAdminUsers] = useState(users);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    async function loadUsers() {
      try {
        const response = await api.adminUsers();
        if (response.items?.length) {
          setAdminUsers(
            response.items.map((user) => ({
              id: user.id,
              name: user.full_name || user.email,
              email: user.email,
              role: user.role,
              status: user.is_active ? "Hoạt động" : "Tạm khóa",
              uploads: 0,
              lastActive: user.created_at
                ? new Date(user.created_at).toLocaleString("vi-VN")
                : "",
            })),
          );
        }
        setNotice("");
      } catch {
        setNotice("Đang dùng dữ liệu mẫu. Cần đăng nhập admin để gọi API quản lý user.");
      }
    }
    loadUsers();
  }, []);

  const filteredUsers = useMemo(() => {
    if (!query.trim()) return adminUsers;
    return adminUsers.filter(
      (u) =>
        u.name.toLowerCase().includes(query.toLowerCase()) ||
        u.email.toLowerCase().includes(query.toLowerCase()),
    );
  }, [query, adminUsers]);

  async function toggleUser(user) {
    try {
      const nextActive = user.status !== "Hoạt động";
      await api.updateAdminUser(user.id, { is_active: nextActive });
      setAdminUsers((items) =>
        items.map((item) =>
          item.id === user.id
            ? { ...item, status: nextActive ? "Hoạt động" : "Tạm khóa" }
            : item,
        ),
      );
    } catch (err) {
      setNotice(err.message || "Không cập nhật được user.");
    }
  }

  const filteredDocs = useMemo(() => {
    if (!query.trim()) return documents;
    return documents.filter(
      (d) =>
        d.title.toLowerCase().includes(query.toLowerCase()) ||
        d.owner.toLowerCase().includes(query.toLowerCase()),
    );
  }, [query]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="grid min-h-screen lg:grid-cols-[280px_1fr]">
        <aside className="border-r border-slate-200 bg-white p-4">
          <div className="flex items-center gap-3 rounded-2xl bg-slate-900 p-4 text-white shadow-sm">
            <div className="rounded-xl bg-white/10 p-2">
              <Shield className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-sm font-semibold">Admin Dashboard</h1>
              <p className="text-xs text-slate-300">
                AI Paper Management System
              </p>
            </div>
          </div>

          <div className="mt-6 space-y-2">
            {navItems.map((item) => {
              const Icon = item.icon;
              const activeStyle =
                active === item.key
                  ? "bg-slate-900 text-white"
                  : "text-slate-600 hover:bg-slate-100";
              return (
                <button
                  key={item.key}
                  onClick={() => setActive(item.key)}
                  className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium transition ${activeStyle}`}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </button>
              );
            })}
          </div>

          <Card className="mt-6 rounded-2xl border-0 bg-slate-100 shadow-none">
            <CardContent className="p-4">
              <p className="text-sm font-semibold">Trạng thái hệ thống</p>
              <div className="mt-3 space-y-4 text-sm text-slate-600">
                <div>
                  <div className="mb-1 flex justify-between">
                    <span>Database</span>
                    <span>92%</span>
                  </div>
                  <Progress value={92} />
                </div>
                <div>
                  <div className="mb-1 flex justify-between">
                    <span>Vector Index</span>
                    <span>89%</span>
                  </div>
                  <Progress value={89} />
                </div>
                <div>
                  <div className="mb-1 flex justify-between">
                    <span>Moderation Queue</span>
                    <span>63%</span>
                  </div>
                  <Progress value={63} />
                </div>
              </div>
            </CardContent>
          </Card>
        </aside>

        <main className="p-4 md:p-6 lg:p-8">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-2xl font-semibold tracking-tight">
                Trang quản trị hệ thống
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Dành riêng cho admin để quản lý người dùng, tài liệu, kiểm duyệt
                nội dung và theo dõi hệ thống.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" className="rounded-2xl">
                <Bell className="mr-2 h-4 w-4" />
                Cảnh báo
              </Button>
              <Button className="rounded-2xl">
                <Lock className="mr-2 h-4 w-4" />
                Admin Panel
              </Button>
            </div>
          </div>
          {notice && <div className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{notice}</div>}

          <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard
              title="Tổng người dùng"
              value="1,284"
              sub="+24 tuần này"
              icon={Users}
            />
            <StatCard
              title="Tài liệu hệ thống"
              value="8,426"
              sub="312 chờ duyệt"
              icon={FileText}
            />
            <StatCard
              title="Nội dung bị gắn cờ"
              value="27"
              sub="Cần xử lý ngay"
              icon={AlertTriangle}
            />
            <StatCard
              title="Tỉ lệ uptime"
              value="99.9%"
              sub="7 ngày gần nhất"
              icon={Database}
            />
          </div>

          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="mt-6"
          >
            {active === "overview" && (
              <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Tác vụ quản trị chính</CardTitle>
                    <CardDescription>
                      Những đầu việc admin thường xuyên xử lý trong hệ thống
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-3 md:grid-cols-2">
                    {[
                      [
                        "Quản lý người dùng",
                        "Phân quyền, khóa/mở khóa tài khoản, xem hoạt động gần đây.",
                        UserCog,
                      ],
                      [
                        "Quản lý tài liệu",
                        "Duyệt, ẩn, xóa tài liệu và kiểm tra metadata.",
                        FolderOpen,
                      ],
                      [
                        "Kiểm duyệt nội dung",
                        "Xử lý tài liệu bị gắn cờ hoặc nghi trùng lặp.",
                        CheckCircle2,
                      ],
                      [
                        "Giám sát hệ thống",
                        "Theo dõi database, queue xử lý và hiệu năng nền tảng.",
                        Database,
                      ],
                    ].map(([title, desc, Icon]) => (
                      <div key={title} className="rounded-2xl bg-slate-50 p-4">
                        <div className="flex items-center gap-2">
                          <Icon className="h-4 w-4 text-slate-700" />
                          <p className="font-medium">{title}</p>
                        </div>
                        <p className="mt-2 text-sm text-slate-500">{desc}</p>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Nhật ký hệ thống</CardTitle>
                    <CardDescription>
                      Các sự kiện admin cần chú ý
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {logs.map((item) => (
                      <div
                        key={item}
                        className="rounded-2xl border border-slate-200 p-4"
                      >
                        <p className="text-sm text-slate-700">{item}</p>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </div>
            )}

            {active === "users" && (
              <Card className="rounded-3xl border-0 shadow-sm">
                <CardHeader>
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <CardTitle>Quản lý người dùng</CardTitle>
                      <CardDescription>
                        Tìm kiếm, phân quyền, khóa hoặc xem hoạt động tài khoản
                      </CardDescription>
                    </div>
                    <div className="flex gap-2">
                      <div className="relative w-full md:w-80">
                        <Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
                        <Input
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          placeholder="Tìm theo tên hoặc email..."
                          className="rounded-2xl pl-9"
                        />
                      </div>
                      <Button variant="outline" className="rounded-2xl">
                        <Filter className="mr-2 h-4 w-4" />
                        Lọc
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {filteredUsers.map((user) => (
                    <div
                      key={user.id}
                      className="grid gap-3 rounded-2xl border border-slate-200 p-4 md:grid-cols-[1.5fr_0.9fr_0.9fr_0.9fr_auto] md:items-center"
                    >
                      <div>
                        <p className="font-medium">{user.name}</p>
                        <p className="text-sm text-slate-500">{user.email}</p>
                      </div>
                      <div className="text-sm text-slate-600">{user.role}</div>
                      <div>
                        <Badge variant="secondary" className="rounded-full">
                          {user.status}
                        </Badge>
                      </div>
                      <div className="text-sm text-slate-500">
                        {user.lastActive}
                      </div>
                      <div className="flex gap-2">
                        <Button variant="outline" className="rounded-2xl">
                          Phân quyền
                        </Button>
                        <Button className="rounded-2xl" onClick={() => toggleUser(user)}>
                          {user.status === "Hoạt động" ? "Khóa" : "Mở khóa"}
                        </Button>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}

            {active === "documents" && (
              <Card className="rounded-3xl border-0 shadow-sm">
                <CardHeader>
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <CardTitle>Quản lý tài liệu</CardTitle>
                      <CardDescription>
                        Kiểm tra tài liệu tải lên, trạng thái duyệt và cờ cảnh
                        báo
                      </CardDescription>
                    </div>
                    <div className="relative w-full md:w-80">
                      <Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
                      <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Tìm theo tài liệu hoặc chủ sở hữu..."
                        className="rounded-2xl pl-9"
                      />
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  {filteredDocs.map((doc) => (
                    <div
                      key={doc.id}
                      className="grid gap-3 rounded-2xl border border-slate-200 p-4 md:grid-cols-[1.8fr_0.8fr_0.8fr_0.9fr_auto] md:items-center"
                    >
                      <div>
                        <p className="font-medium">{doc.title}</p>
                        <p className="text-sm text-slate-500">
                          Người tải: {doc.owner} · {doc.pages} trang
                        </p>
                      </div>
                      <div>
                        <Badge variant="secondary" className="rounded-full">
                          {doc.status}
                        </Badge>
                      </div>
                      <div className="text-sm text-slate-600">{doc.flag}</div>
                      <div className="text-sm text-slate-500">
                        {doc.updated}
                      </div>
                      <div className="flex gap-2">
                        <Button variant="outline" className="rounded-2xl">
                          <Eye className="h-4 w-4" />
                        </Button>
                        <Button variant="outline" className="rounded-2xl">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}

            {active === "moderation" && (
              <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Hàng đợi kiểm duyệt</CardTitle>
                    <CardDescription>
                      Admin xử lý tài liệu mới hoặc nội dung bị gắn cờ
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Tabs value={tab} onValueChange={setTab}>
                      <TabsList className="grid w-full grid-cols-3 rounded-2xl">
                        <TabsTrigger value="pending">Chờ duyệt</TabsTrigger>
                        <TabsTrigger value="flagged">Bị gắn cờ</TabsTrigger>
                        <TabsTrigger value="duplicate">
                          Nghi trùng lặp
                        </TabsTrigger>
                      </TabsList>
                    </Tabs>
                    <Textarea
                      className="min-h-[180px] rounded-2xl"
                      placeholder="Ghi chú kiểm duyệt, lý do ẩn hoặc từ chối tài liệu..."
                    />
                    <div className="grid grid-cols-2 gap-3">
                      <Button variant="outline" className="w-full rounded-2xl">
                        Từ chối
                      </Button>
                      <Button className="w-full rounded-2xl">Phê duyệt</Button>
                    </div>
                  </CardContent>
                </Card>

                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Tài liệu cần xử lý</CardTitle>
                    <CardDescription>
                      Danh sách nội dung admin cần xem xét ngay
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {documents
                      .filter((d) => d.status !== "Đã duyệt")
                      .map((doc) => (
                        <div
                          key={doc.id}
                          className="rounded-2xl border border-slate-200 p-4"
                        >
                          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                            <div>
                              <p className="font-medium">{doc.title}</p>
                              <p className="text-sm text-slate-500">
                                Người tải: {doc.owner}
                              </p>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge
                                variant="secondary"
                                className="rounded-full"
                              >
                                {doc.status}
                              </Badge>
                              <Button variant="outline" className="rounded-2xl">
                                Xem chi tiết
                              </Button>
                            </div>
                          </div>
                        </div>
                      ))}
                  </CardContent>
                </Card>
              </div>
            )}

            {active === "analytics" && (
              <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Chỉ số quản trị</CardTitle>
                    <CardDescription>
                      Số liệu tổng quan toàn hệ thống
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {[
                      ["Tài khoản mới tuần này", "24"],
                      ["Tài liệu chờ duyệt", "312"],
                      ["Lượt xử lý AI hôm nay", "1,842"],
                      ["Báo cáo vi phạm", "9"],
                    ].map(([k, v]) => (
                      <div
                        key={k}
                        className="flex items-center justify-between rounded-2xl bg-slate-50 p-4"
                      >
                        <span>{k}</span>
                        <span className="text-xl font-semibold">{v}</span>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Biểu đồ hoạt động hệ thống</CardTitle>
                    <CardDescription>
                      Minh họa lượt tải lên và xử lý theo ngày
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid min-h-[320px] grid-cols-7 items-end gap-3 rounded-3xl bg-slate-50 p-6">
                      {[48, 72, 60, 85, 67, 91, 74].map((h, idx) => (
                        <div
                          key={idx}
                          className="flex flex-col items-center gap-2"
                        >
                          <div
                            className="w-full rounded-t-2xl bg-slate-800"
                            style={{ height: `${h * 2}px` }}
                          />
                          <span className="text-xs text-slate-500">
                            T{idx + 1}
                          </span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </div>
            )}

            {active === "system" && (
              <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Trạng thái hạ tầng</CardTitle>
                    <CardDescription>
                      Theo dõi queue, index và kết nối dịch vụ
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {[
                      ["API Server", 97],
                      ["Embedding Service", 84],
                      ["Vector Database", 89],
                      ["Storage", 76],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <div className="mb-2 flex justify-between text-sm">
                          <span>{label}</span>
                          <span>{value}%</span>
                        </div>
                        <Progress value={value} />
                      </div>
                    ))}
                  </CardContent>
                </Card>

                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Hành động nhanh</CardTitle>
                    <CardDescription>
                      Các thao tác kỹ thuật của admin
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="grid gap-3 md:grid-cols-2">
                    {[
                      ["Re-index dữ liệu", Database],
                      ["Khóa tài khoản", Ban],
                      ["Kiểm tra log", Eye],
                      ["Dọn tài liệu lỗi", Trash2],
                    ].map(([label, Icon]) => (
                      <Button
                        key={label}
                        variant="outline"
                        className="justify-start rounded-2xl"
                      >
                        <Icon className="mr-2 h-4 w-4" />
                        {label}
                      </Button>
                    ))}
                  </CardContent>
                </Card>
              </div>
            )}

            {active === "settings" && (
              <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Cấu hình quản trị</CardTitle>
                    <CardDescription>
                      Thiết lập chính sách kiểm duyệt và quyền hệ thống
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Input
                      placeholder="Ngưỡng cảnh báo tài liệu trùng lặp"
                      className="rounded-2xl"
                    />
                    <Input
                      placeholder="Giới hạn dung lượng upload"
                      className="rounded-2xl"
                    />
                    <Textarea
                      className="min-h-[160px] rounded-2xl"
                      placeholder="Nội dung thông báo chung cho người dùng..."
                    />
                    <Button className="w-full rounded-2xl">Lưu cấu hình</Button>
                  </CardContent>
                </Card>

                <Card className="rounded-3xl border-0 shadow-sm">
                  <CardHeader>
                    <CardTitle>Quy tắc hệ thống</CardTitle>
                    <CardDescription>
                      Những nguyên tắc admin đang áp dụng
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {[
                      "Tài liệu mới phải qua bước kiểm duyệt trước khi hiển thị công khai.",
                      "Tài khoản vi phạm nhiều lần sẽ bị khóa tạm thời hoặc vĩnh viễn.",
                      "Tài liệu nghi trùng lặp được chuyển vào hàng đợi kiểm tra riêng.",
                      "Chỉ admin và moderator mới có quyền duyệt và xóa tài liệu.",
                    ].map((rule) => (
                      <div
                        key={rule}
                        className="rounded-2xl border border-slate-200 p-4 text-sm text-slate-600"
                      >
                        {rule}
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </div>
            )}
          </motion.div>
        </main>
      </div>
    </div>
  );
}
