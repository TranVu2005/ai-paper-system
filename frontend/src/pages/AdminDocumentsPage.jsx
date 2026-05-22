import { useEffect, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import AdminLayout from "@/components/admin/AdminLayout";
import DataTable from "@/components/admin/DataTable";
import StatCard from "@/components/admin/StatCard";
import { deleteDocument, getAdminDocuments, getAdminStats, reprocessDocument } from "@/services/adminService";
import { api } from "@/lib/api";

function Badge({ children, tone = "slate" }) {
  const cls = {
    blue: "bg-blue-50 text-blue-700 border-blue-200",
    amber: "bg-amber-50 text-amber-700 border-amber-200",
    green: "bg-emerald-50 text-emerald-700 border-emerald-200",
    red: "bg-rose-50 text-rose-700 border-rose-200",
    slate: "bg-slate-100 text-slate-700 border-slate-200",
    violet: "bg-violet-50 text-violet-700 border-violet-200",
  }[tone];
  return <span className={`rounded-full border px-2 py-1 text-xs ${cls}`}>{children}</span>;
}

const statusTone = {
  uploaded: "blue",
  processing: "amber",
  processed: "green",
  failed: "red",
};

export default function AdminDocumentsPage() {
  const [overview, setOverview] = useState(null);
  const [docs, setDocs] = useState({ items: [], total: 0, page: 1, page_size: 10 });
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [fileType, setFileType] = useState("");
  const [owner, setOwner] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionDocId, setActionDocId] = useState(null);
  const [detailDoc, setDetailDoc] = useState(null);
  const [notice, setNotice] = useState("");

  async function load(page = 1) {
    setLoading(true);
    setError("");
    try {
      const [ov, result] = await Promise.all([
        getAdminStats(),
        getAdminDocuments({
          page,
          page_size: 10,
          q: query || undefined,
          status: status || undefined,
          file_type: fileType || undefined,
          owner: owner || undefined,
        }),
      ]);
      setOverview(ov || {});
      setDocs(result || { items: [], total: 0, page, page_size: 10 });
    } catch {
      setError("Không tải được danh sách tài liệu.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(1);
  }, []);

  const k = overview?.kpis || {};
  const totalPages = Math.max(1, Math.ceil((docs.total || 0) / (docs.page_size || 10)));

  async function handleReprocess(doc) {
    const ok = window.confirm("Bạn muốn xử lý lại tài liệu này?");
    if (!ok) return;
    try {
      await reprocessDocument(doc.id);
      setNotice("Đã gửi yêu cầu xử lý lại.");
      setActionDocId(null);
    } catch {
      setNotice("Không thể xử lý lại tài liệu.");
    }
  }

  async function handleDelete(doc) {
    const ok = window.confirm("Bạn chắc chắn muốn xóa tài liệu này?");
    if (!ok) return;
    try {
      await deleteDocument(doc.id);
      setNotice("Đã xóa tài liệu.");
      setActionDocId(null);
      await load(docs.page);
    } catch {
      setNotice("Không thể xóa tài liệu.");
    }
  }

  return (
    <AdminLayout
      title="Quản lý tài liệu"
      description="Theo dõi trạng thái xử lý, metadata và nguồn tài liệu trong hệ thống"
      onRefresh={() => load(docs.page)}
    >
      {notice ? <div className="rounded-xl border border-slate-200 bg-white p-3 text-sm text-slate-700">{notice}</div> : null}
      {error ? <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div> : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <StatCard title="Tổng số tài liệu" value={Number(k.total_documents || 0)} />
        <StatCard title="Đã xử lý" value={Number(k.processed || 0)} />
        <StatCard title="Đang xử lý" value={Number(k.processing || 0)} />
        <StatCard title="Lỗi" value={Number(k.failed || 0)} />
        <StatCard title="Tổng chunks" value={0} hint="TODO: cần API chunk count tổng." />
        <StatCard title="Tổng summaries" value={Number(k.total_summaries || 0)} />
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="grid gap-2 lg:grid-cols-6">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm theo tên tài liệu, tác giả, DOI" className="h-10 rounded-lg border border-slate-300 px-3 text-sm" />
          <select value={fileType} onChange={(e) => setFileType(e.target.value)} className="h-10 rounded-lg border border-slate-300 px-3 text-sm">
            <option value="">Tất cả file type</option>
            <option value="application/pdf">PDF</option>
            <option value="text/plain">TXT</option>
            <option value="application/vnd.openxmlformats-officedocument.wordprocessingml.document">DOCX</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-10 rounded-lg border border-slate-300 px-3 text-sm">
            <option value="">Tất cả trạng thái</option>
            <option value="uploaded">Uploaded</option>
            <option value="processing">Processing</option>
            <option value="processed">Processed</option>
            <option value="failed">Failed</option>
          </select>
          <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Lọc theo người tải lên" className="h-10 rounded-lg border border-slate-300 px-3 text-sm" />
          <button type="button" onClick={() => load(1)} className="rounded-lg border border-slate-300 px-3 text-sm hover:bg-slate-50">Lọc</button>
          <button type="button" onClick={() => load(docs.page)} className="rounded-lg border border-slate-300 px-3 text-sm hover:bg-slate-50">Refresh</button>
        </div>
      </section>

      <DataTable
        columns={[
          { key: "name", label: "Tên tài liệu" },
          { key: "author", label: "Tác giả" },
          { key: "type", label: "Loại file" },
          { key: "status", label: "Trạng thái" },
          { key: "size", label: "Số trang / kích thước" },
          { key: "owner", label: "Người tải lên" },
          { key: "date", label: "Ngày tải lên" },
          { key: "actions", label: "Hành động" },
        ]}
      >
        <tbody>
          {loading ? <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-500">Đang tải dữ liệu...</td></tr> : null}
          {!loading && !docs.items.length ? <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-500">Không có tài liệu.</td></tr> : null}
          {docs.items.map((doc) => (
            <tr key={doc.id} className="border-t border-slate-200">
              <td className="max-w-[220px] truncate px-3 py-2" title={doc.filename}>{doc.filename || "-"}</td>
              <td className="max-w-[160px] truncate px-3 py-2" title={doc.authors}>{doc.authors || "-"}</td>
              <td className="px-3 py-2"><Badge tone="violet">{doc.file_type || "-"}</Badge></td>
              <td className="px-3 py-2"><Badge tone={statusTone[doc.status] || "slate"}>{doc.status || "-"}</Badge></td>
              <td className="px-3 py-2">{doc.page_count ? `${doc.page_count} trang` : doc.file_size_mb ? `${doc.file_size_mb} MB` : "-"}</td>
              <td className="px-3 py-2">{doc.owner_name || doc.owner_email || "-"}</td>
              <td className="px-3 py-2">{doc.created_at ? new Date(doc.created_at).toLocaleString("vi-VN") : "-"}</td>
              <td className="px-3 py-2">
                <div className="relative">
                  <button type="button" onClick={() => setActionDocId(actionDocId === doc.id ? null : doc.id)} className="rounded-md border border-slate-300 p-1.5 hover:bg-slate-50">
                    <MoreHorizontal className="h-4 w-4" />
                  </button>
                  {actionDocId === doc.id ? (
                    <div className="absolute right-0 z-20 mt-1 w-44 rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
                      <button type="button" onClick={() => setDetailDoc(doc)} className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50">Xem chi tiết</button>
                      <button type="button" onClick={() => api.downloadDocument(doc.id, doc.filename || "document")} className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50">Tải xuống</button>
                      <button type="button" onClick={() => handleReprocess(doc)} className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50">Xử lý lại</button>
                      <button type="button" onClick={() => handleDelete(doc)} className="w-full rounded px-2 py-1.5 text-left text-sm text-rose-600 hover:bg-rose-50">Xóa</button>
                    </div>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>

      <div className="flex items-center justify-end gap-2 text-sm">
        <button type="button" disabled={docs.page <= 1} onClick={() => load(docs.page - 1)} className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40">Prev</button>
        <span>{docs.page}/{totalPages}</span>
        <button type="button" disabled={docs.page >= totalPages} onClick={() => load(docs.page + 1)} className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40">Next</button>
      </div>

      {detailDoc ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-3xl rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-lg font-semibold text-slate-900">Chi tiết tài liệu</h3>
              <button type="button" onClick={() => setDetailDoc(null)} className="rounded-md border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50">Đóng</button>
            </div>
            <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
              <div><p className="text-slate-500">Tên tài liệu</p><p className="font-medium">{detailDoc.filename || "-"}</p></div>
              <div><p className="text-slate-500">Tác giả</p><p className="font-medium">{detailDoc.authors || "-"}</p></div>
              <div><p className="text-slate-500">DOI</p><p className="font-medium">{detailDoc.doi || "-"}</p></div>
              <div><p className="text-slate-500">File type</p><p className="font-medium">{detailDoc.file_type || "-"}</p></div>
              <div><p className="text-slate-500">File size</p><p className="font-medium">{detailDoc.file_size_mb ? `${detailDoc.file_size_mb} MB` : "-"}</p></div>
              <div><p className="text-slate-500">Số trang</p><p className="font-medium">{detailDoc.page_count || "-"}</p></div>
              <div><p className="text-slate-500">Trạng thái</p><p className="font-medium">{detailDoc.status || "-"}</p></div>
              <div><p className="text-slate-500">Người tải lên</p><p className="font-medium">{detailDoc.owner_name || detailDoc.owner_email || "-"}</p></div>
              <div><p className="text-slate-500">Ngày tải lên</p><p className="font-medium">{detailDoc.created_at ? new Date(detailDoc.created_at).toLocaleString("vi-VN") : "-"}</p></div>
              <div><p className="text-slate-500">Summary ngắn</p><p className="font-medium">{detailDoc.summary || "-"}</p></div>
              <div><p className="text-slate-500">Lỗi xử lý</p><p className="font-medium">{detailDoc.error_message || "-"}</p></div>
            </div>
          </div>
        </div>
      ) : null}
    </AdminLayout>
  );
}
