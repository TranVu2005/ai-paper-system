import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Download, Eye, FileText, Search, Sparkles, Trash2, Upload } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { documents as fallbackDocuments } from "@/lib/mockData";
import { api } from "@/lib/api";
import { mapApiDocuments } from "@/lib/documentMapper";

const statusLabel = {
  uploaded: "Mới tải lên",
  processing: "Đang xử lý",
  processed: "Sẵn sàng",
};

export default function LibraryPage() {
  const [documents, setDocuments] = useState(fallbackDocuments);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadDocuments() {
      try {
        const response = await api.listDocuments({ page_size: 50 });
        setDocuments(mapApiDocuments(response, fallbackDocuments));
        setMessage("");
      } catch {
        setMessage("Đang dùng dữ liệu mẫu. Hãy đăng nhập và chạy backend để lấy tài liệu thật.");
      }
    }
    loadDocuments();
  }, []);

  async function handleDelete(documentId) {
    try {
      await api.deleteDocument(documentId);
      setDocuments((items) => items.filter((item) => item.id !== documentId));
      setMessage("Đã xóa tài liệu.");
    } catch (err) {
      setMessage(err.message || "Không xóa được tài liệu.");
    }
  }

  async function handleDownload(doc) {
    try {
      await api.downloadDocument(doc.id, doc.filename);
    } catch (err) {
      setMessage(err.message || "Không tải được file.");
    }
  }

  const filteredDocs = useMemo(() => {
    return documents.filter((doc) => {
      const matchQuery = [doc.title, doc.filename, doc.type, ...doc.topics, ...doc.authors]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase());
      const matchStatus = status === "all" || doc.status === status;
      return matchQuery && matchStatus;
    });
  }, [documents, query, status]);

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
          <div>
            <h1 className="text-xl font-semibold">Thư viện tài liệu</h1>
            <p className="text-sm text-zinc-500">Lưu trữ, hiển thị, xem, sửa, xóa và tải file gốc.</p>
          </div>
          <Link to="/upload"><Button><Upload className="mr-2 h-4 w-4" />Tải lên</Button></Link>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8 lg:px-8">
        <Card className="rounded-lg border-zinc-200 shadow-sm">
          <CardHeader>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <CardTitle>Danh sách tài liệu</CardTitle>
                <CardDescription>Dữ liệu này ánh xạ với `GET /api/v1/documents/` và `GET /api/v1/documents/{'{id}'}/file`.</CardDescription>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <div className="relative sm:w-80">
                  <Search className="absolute left-3 top-3 h-4 w-4 text-zinc-400" />
                  <Input value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder="Tìm tiêu đề, tác giả, chủ đề..." />
                </div>
              </div>
            </div>
            {message && <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{message}</div>}
            <div className="flex flex-wrap gap-2 pt-2">
              {[
                ["all", "Tất cả"],
                ["processed", "Sẵn sàng"],
                ["processing", "Đang xử lý"],
                ["uploaded", "Mới tải lên"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setStatus(key)}
                  className={`rounded-md border px-3 py-2 text-sm ${status === key ? "border-zinc-950 bg-zinc-950 text-white" : "border-zinc-200 bg-white text-zinc-700"}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {filteredDocs.map((doc) => (
              <div key={doc.id} className="grid gap-4 rounded-lg border border-zinc-200 bg-white p-4 xl:grid-cols-[1fr_auto_auto] xl:items-center">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <FileText className="h-4 w-4 text-zinc-600" />
                    <p className="font-medium">{doc.title}</p>
                    <Badge variant="secondary">{doc.type}</Badge>
                    <Badge>{statusLabel[doc.status]}</Badge>
                  </div>
                  <p className="mt-2 text-sm text-zinc-500">
                    {doc.filename} · {doc.year} · {doc.pages} trang · {doc.authors.join(", ")}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {doc.topics.map((topic) => (
                      <span key={topic} className="rounded-md bg-zinc-100 px-2 py-1 text-xs text-zinc-700">{topic}</span>
                    ))}
                  </div>
                </div>

                <div className="text-sm text-zinc-500">Cập nhật: {doc.updated}</div>

                <div className="flex flex-wrap gap-2">
                  <Link to={`/document/${doc.id}`}><Button variant="outline"><Eye className="mr-2 h-4 w-4" />Xem</Button></Link>
                  <Button variant="outline" onClick={() => handleDownload(doc)}><Download className="mr-2 h-4 w-4" />File</Button>
                  <Button variant="outline" onClick={() => handleDelete(doc.id)}><Trash2 className="mr-2 h-4 w-4" />Xóa</Button>
                  <Link to={`/document/${doc.id}`}><Button><Sparkles className="mr-2 h-4 w-4" />AI</Button></Link>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
