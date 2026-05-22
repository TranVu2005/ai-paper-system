import { Navigate } from "react-router-dom";
import { api } from "@/lib/api";
import AdminSidebar from "./AdminSidebar";
import AdminHeader from "./AdminHeader";

export default function AdminLayout({ title, description, onRefresh, rightSlot = null, children }) {
  const token = api.getToken();
  const currentUser = api.getCurrentUser();

  if (!token || !currentUser) return <Navigate to="/login" replace />;
  if (currentUser.role !== "admin") return <Navigate to="/home" replace />;

  return (
    <div className="min-h-screen bg-slate-100/70 text-slate-900">
      <div className="mx-auto grid max-w-[1560px] gap-4 p-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <AdminSidebar />
        <main className="min-w-0 space-y-4">
          <AdminHeader
            breadcrumb={`Admin / ${title}`}
            title={title}
            description={description}
            onRefresh={onRefresh}
            rightSlot={rightSlot}
          />
          <div className="space-y-4 pb-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
