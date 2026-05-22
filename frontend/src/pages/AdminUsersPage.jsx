import { useEffect, useMemo, useState } from "react";
import { MoreHorizontal, UserPlus } from "lucide-react";
import AdminLayout from "@/components/admin/AdminLayout";
import StatCard from "@/components/admin/StatCard";
import DataTable from "@/components/admin/DataTable";
import { getAdminStats, getAdminUsers, lockUser, unlockUser, updateUserRole, deleteUser } from "@/services/adminService";

function Badge({ children, tone = "slate" }) {
  const cls = {
    green: "bg-emerald-50 text-emerald-700 border-emerald-200",
    red: "bg-rose-50 text-rose-700 border-rose-200",
    violet: "bg-violet-50 text-violet-700 border-violet-200",
    slate: "bg-slate-100 text-slate-700 border-slate-200",
  }[tone];
  return <span className={`rounded-full border px-2 py-1 text-xs ${cls}`}>{children}</span>;
}

export default function AdminUsersPage() {
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState({ items: [], total: 0, page: 1, page_size: 10 });
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionUserId, setActionUserId] = useState(null);
  const [notice, setNotice] = useState("");

  async function load(page = 1) {
    setLoading(true);
    setError("");
    try {
      const [ov, result] = await Promise.all([
        getAdminStats(),
        getAdminUsers({
          page,
          page_size: 10,
          q: query || undefined,
          role: role || undefined,
          is_active: status === "" ? undefined : status === "active",
        }),
      ]);
      setOverview(ov || {});
      setUsers(result || { items: [], total: 0, page, page_size: 10 });
    } catch {
      setError("Không tải được danh sách người dùng.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(1);
  }, []);

  const k = overview?.kpis || {};
  const totalPages = Math.max(1, Math.ceil((users.total || 0) / (users.page_size || 10)));

  async function handleToggleLock(user) {
    const willLock = user.is_active;
    const ok = window.confirm(willLock ? "Khóa người dùng này?" : "Mở khóa người dùng này?");
    if (!ok) return;
    try {
      if (willLock) await lockUser(user.id);
      else await unlockUser(user.id);
      setNotice("Đã cập nhật trạng thái người dùng.");
      setActionUserId(null);
      await load(users.page);
    } catch {
      setNotice("Không thể cập nhật trạng thái người dùng.");
    }
  }

  async function handleRoleChange(user) {
    const nextRole = user.role === "admin" ? "user" : "admin";
    const ok = window.confirm(`Đổi vai trò thành ${nextRole}?`);
    if (!ok) return;
    try {
      await updateUserRole(user.id, nextRole);
      setNotice("Đã đổi vai trò người dùng.");
      setActionUserId(null);
      await load(users.page);
    } catch {
      setNotice("Không thể đổi vai trò người dùng.");
    }
  }

  async function handleDelete(user) {
    const ok = window.confirm("Bạn chắc chắn muốn xóa người dùng này?");
    if (!ok) return;
    try {
      await deleteUser(user.id);
      setNotice("Đã xóa người dùng.");
      setActionUserId(null);
      await load(users.page);
    } catch {
      setNotice("Chức năng xóa người dùng chưa khả dụng.");
    }
  }

  const cards = useMemo(() => ([
    { title: "Tổng số người dùng", value: Number(k.total_users || 0) },
    { title: "Đang hoạt động", value: Number(k.active_users || 0) },
    { title: "Bị khóa", value: Number(k.inactive_users || 0) },
    { title: "Đăng nhập hôm nay", value: 0 },
  ]), [k]);

  return (
    <AdminLayout
      title="Quản lý người dùng"
      description="Theo dõi, tìm kiếm và quản lý tài khoản người dùng trong hệ thống"
      onRefresh={() => load(users.page)}
      rightSlot={(
        <button type="button" className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-2 text-sm text-white hover:bg-indigo-700">
          <UserPlus className="h-4 w-4" />
          Thêm người dùng
        </button>
      )}
    >
      {notice ? <div className="rounded-xl border border-slate-200 bg-white p-3 text-sm text-slate-700">{notice}</div> : null}
      {error ? <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div> : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => <StatCard key={card.title} title={card.title} value={card.value} />)}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="grid gap-2 lg:grid-cols-5">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm theo tên hoặc email" className="h-10 rounded-lg border border-slate-300 px-3 text-sm" />
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-10 rounded-lg border border-slate-300 px-3 text-sm">
            <option value="">Tất cả trạng thái</option>
            <option value="active">Đang hoạt động</option>
            <option value="locked">Bị khóa</option>
          </select>
          <select value={role} onChange={(e) => setRole(e.target.value)} className="h-10 rounded-lg border border-slate-300 px-3 text-sm">
            <option value="">Tất cả vai trò</option>
            <option value="admin">Admin</option>
            <option value="user">User</option>
          </select>
          <button type="button" onClick={() => load(1)} className="rounded-lg border border-slate-300 px-3 text-sm hover:bg-slate-50">Lọc</button>
          <button
            type="button"
            onClick={() => {
              const header = "name,email,role,status,created_at,last_login\n";
              const rows = users.items
                .map((u) => `${u.full_name || ""},${u.email || ""},${u.role || ""},${u.is_active ? "active" : "locked"},${u.created_at || ""},${u.last_login_at || ""}`)
                .join("\n");
              const blob = new Blob([header + rows], { type: "text/csv;charset=utf-8;" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = "admin_users.csv";
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="rounded-lg border border-slate-300 px-3 text-sm hover:bg-slate-50"
          >
            Export CSV
          </button>
        </div>
      </section>

      <DataTable
        columns={[
          { key: "user", label: "Người dùng" },
          { key: "email", label: "Email" },
          { key: "role", label: "Vai trò" },
          { key: "status", label: "Trạng thái" },
          { key: "created", label: "Ngày tạo" },
          { key: "last_login", label: "Lần đăng nhập cuối" },
          { key: "actions", label: "Hành động" },
        ]}
      >
        <tbody>
          {loading ? (
            <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-500">Đang tải dữ liệu...</td></tr>
          ) : null}
          {!loading && !users.items.length ? (
            <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-500">Không có người dùng.</td></tr>
          ) : null}
          {users.items.map((user) => (
            <tr key={user.id} className="border-t border-slate-200">
              <td className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-100 text-xs font-semibold text-indigo-700">
                    {(user.full_name || user.email || "?").slice(0, 1).toUpperCase()}
                  </span>
                  <span className="max-w-[200px] truncate">{user.full_name || "Chưa có tên"}</span>
                </div>
              </td>
              <td className="px-3 py-2">{user.email || "-"}</td>
              <td className="px-3 py-2"><Badge tone={user.role === "admin" ? "violet" : "slate"}>{user.role || "user"}</Badge></td>
              <td className="px-3 py-2"><Badge tone={user.is_active ? "green" : "red"}>{user.is_active ? "Đang hoạt động" : "Bị khóa"}</Badge></td>
              <td className="px-3 py-2">{user.created_at ? new Date(user.created_at).toLocaleDateString("vi-VN") : "-"}</td>
              <td className="px-3 py-2">{user.last_login_at ? new Date(user.last_login_at).toLocaleString("vi-VN") : "-"}</td>
              <td className="px-3 py-2">
                <div className="relative">
                  <button type="button" onClick={() => setActionUserId(actionUserId === user.id ? null : user.id)} className="rounded-md border border-slate-300 p-1.5 hover:bg-slate-50">
                    <MoreHorizontal className="h-4 w-4" />
                  </button>
                  {actionUserId === user.id ? (
                    <div className="absolute right-0 z-20 mt-1 w-44 rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
                      <button type="button" className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50">Xem chi tiết</button>
                      <button type="button" onClick={() => handleRoleChange(user)} className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50">Đổi vai trò</button>
                      <button type="button" onClick={() => handleToggleLock(user)} className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50">{user.is_active ? "Khóa" : "Mở khóa"}</button>
                      <button type="button" onClick={() => handleDelete(user)} className="w-full rounded px-2 py-1.5 text-left text-sm text-rose-600 hover:bg-rose-50">Xóa</button>
                    </div>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>

      <div className="flex items-center justify-end gap-2 text-sm">
        <button type="button" disabled={users.page <= 1} onClick={() => load(users.page - 1)} className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40">Prev</button>
        <span>{users.page}/{totalPages}</span>
        <button type="button" disabled={users.page >= totalPages} onClick={() => load(users.page + 1)} className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40">Next</button>
      </div>
    </AdminLayout>
  );
}
