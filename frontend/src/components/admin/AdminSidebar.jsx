import { Link, useLocation } from "react-router-dom";
import { FileText, LayoutDashboard, Settings, Users, FolderKanban } from "lucide-react";

const items = [
  { key: "dashboard", label: "Dashboard", to: "/admin/dashboard", icon: LayoutDashboard },
  { key: "users", label: "Users", to: "/admin/users", icon: Users },
  { key: "documents", label: "Documents", to: "/admin/documents", icon: FileText },
  { key: "workspaces", label: "Workspaces", to: "/home", icon: FolderKanban },
  { key: "settings", label: "Settings", to: "/profile", icon: Settings },
];

export default function AdminSidebar() {
  const location = useLocation();
  return (
    <aside className="sticky top-4 h-fit rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
      <p className="px-2 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Admin</p>
      <nav className="space-y-1">
        {items.map((item) => {
          const Icon = item.icon;
          const active = location.pathname === item.to;
          return (
            <Link
              key={item.key}
              to={item.to}
              className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
                active ? "bg-indigo-50 text-indigo-700" : "text-slate-700 hover:bg-slate-100"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
