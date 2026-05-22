import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { AlertCircle, CheckCircle2, FileText, FolderOpen, Library, Loader2, Tag, Upload } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { documents } from "@/lib/mockData";
import { api } from "@/lib/api";

const steps = [
  ["Lưu file gốc", "Backend lưu PDF/DOCX/TXT để xem và download lại.", CheckCircle2],
  ["Trích xuất nội dung", "Ingestion chạy nền ngay sau upload để lấy text và metadata.", FileText],
  ["Sinh JSON + đồng bộ DB", "Pipeline tạo unified JSON và ghi metadata/artifact vào Neon.", Loader2],
  ["Sẵn sàng khai thác", "Có thể tóm tắt, hỏi đáp, KG, gợi ý và search.", CheckCircle2],
];

export default function UploadPage() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [metadata, setMetadata] = useState({
    title: "",
    source: "",
    authors: "",
    topics: "",
    abstract: "",
  });
  const navigate = useNavigate();
  const fileName = selectedFile?.name || "Chưa chọn file";

  function updateField(field, value) {
    setMetadata((current) => ({ ...current, [field]: value }));
  }

  function splitList(value) {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  async function handleUpload() {
    if (!selectedFile) return;
    setMessage("");
    setError("");
    setUploading(true);
    try {
      const response = await api.uploadDocument(selectedFile);
      if (response.document_id) {
        const payload = {
          title: metadata.title || selectedFile.name,
          source: metadata.source || null,
          authors: splitList(metadata.authors),
          topics: splitList(metadata.topics),
          keywords: splitList(metadata.topics),
          abstract: metadata.abstract || null,
        };
        await api.updateDocumentMetadata(response.document_id, payload);
      }
      setMessage("Tải lên thành công. Ingestion đang chạy ngầm để sinh JSON và đồng bộ dữ liệu.");
      if (response.document_id) {
        setTimeout(() => navigate(`/document/${response.document_id}`), 600);
      }
    } catch (err) {
      setError(err.message || "Tải lên thất bại");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
          <div>
            <h1 className="text-xl font-semibold">Tải lên tài liệu</h1>
            <p className="text-sm text-zinc-500">Hỗ trợ tài liệu tiếng Việt định dạng PDF, DOCX và TXT.</p>
          </div>
          <Link to="/library"><Button variant="outline"><Library className="mr-2 h-4 w-4" />Thư viện</Button></Link>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8 lg:px-8">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
          <Card className="rounded-lg border-zinc-200 shadow-sm">
            <CardHeader>
              <CardTitle>Thông tin tài liệu</CardTitle>
              <CardDescription>Upload file và bổ sung metadata để phục vụ tìm kiếm, thống kê và Knowledge Graph.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <label className="block cursor-pointer rounded-lg border-2 border-dashed border-zinc-300 bg-zinc-50 p-8 text-center transition hover:border-zinc-500">
                <Upload className="mx-auto h-9 w-9 text-zinc-500" />
                <p className="mt-3 font-medium">Chọn hoặc kéo thả file</p>
                <p className="mt-1 text-sm text-zinc-500">PDF, DOCX, TXT · tối đa 20MB</p>
                <input
                  type="file"
                  accept=".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  className="hidden"
                  onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
                />
              </label>

              <div className="rounded-lg border border-zinc-200 bg-white p-4">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-zinc-600" />
                  <span className="font-medium">{fileName}</span>
                </div>
                <p className="mt-2 text-sm text-zinc-500">File này sẽ được gửi đến `POST /api/v1/documents/upload`.</p>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Tiêu đề</label>
                  <Input
                    value={metadata.title}
                    onChange={(event) => updateField("title", event.target.value)}
                    placeholder="Tên bài báo hoặc tài liệu"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Nguồn / thư mục</label>
                  <div className="relative">
                    <FolderOpen className="absolute left-3 top-3 h-4 w-4 text-zinc-400" />
                    <Input
                      className="pl-9"
                      value={metadata.source}
                      onChange={(event) => updateField("source", event.target.value)}
                      placeholder="Ví dụ: NLP, Data Mining"
                    />
                  </div>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Tác giả</label>
                  <Input
                    value={metadata.authors}
                    onChange={(event) => updateField("authors", event.target.value)}
                    placeholder="Nguyễn Văn A, Trần Thị B"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Chủ đề / từ khóa</label>
                  <div className="relative">
                    <Tag className="absolute left-3 top-3 h-4 w-4 text-zinc-400" />
                    <Input
                      className="pl-9"
                      value={metadata.topics}
                      onChange={(event) => updateField("topics", event.target.value)}
                      placeholder="RAG, KG, Semantic Search"
                    />
                  </div>
                </div>
              </div>

              <Textarea
                className="min-h-[120px]"
                value={metadata.abstract}
                onChange={(event) => updateField("abstract", event.target.value)}
                placeholder="Tóm tắt hoặc ghi chú về tài liệu..."
              />

              <div className="flex flex-wrap gap-3">
                <Button disabled={!selectedFile || uploading} onClick={handleUpload}>
                  <Upload className="mr-2 h-4 w-4" />
                  {uploading ? "Đang tải lên..." : "Bắt đầu tải lên"}
                </Button>
                <Button variant="outline" onClick={() => setSelectedFile(null)}>Xóa lựa chọn</Button>
              </div>
              {message && <div className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>}
              {error && <div className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</div>}
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card className="rounded-lg border-zinc-200 shadow-sm">
              <CardHeader>
                <CardTitle>Pipeline sau upload</CardTitle>
                <CardDescription>Những việc backend ingestion tự động thực hiện sau upload.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {steps.map(([title, desc, Icon], index) => (
                  <div key={title} className="grid grid-cols-[auto_1fr] gap-3 rounded-lg border border-zinc-200 p-4">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-zinc-100 text-sm font-semibold">{index + 1}</div>
                    <div>
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-zinc-700" />
                        <p className="font-medium">{title}</p>
                      </div>
                      <p className="mt-1 text-sm text-zinc-500">{desc}</p>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card className="rounded-lg border-zinc-200 shadow-sm">
              <CardHeader>
                <CardTitle>Hàng đợi xử lý</CardTitle>
                <CardDescription>Trạng thái mẫu của các tài liệu gần đây.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {documents.slice(0, 3).map((doc, index) => {
                  const progress = doc.status === "processed" ? 100 : doc.status === "processing" ? 58 : 20;
                  return (
                    <div key={doc.id} className="rounded-lg border border-zinc-200 p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="font-medium">{doc.filename}</p>
                          <p className="text-sm text-zinc-500">Bước {index + 1}: {doc.status}</p>
                        </div>
                        <Badge variant="secondary">{progress}%</Badge>
                      </div>
                      <div className="mt-3"><Progress value={progress} /></div>
                    </div>
                  );
                })}
              </CardContent>
            </Card>

            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <div className="flex items-start gap-3">
                <AlertCircle className="mt-0.5 h-5 w-5" />
                <p>Nếu file vừa upload chưa tóm tắt được ngay, hãy chờ ingestion hoàn tất và trạng thái tài liệu cập nhật.</p>
              </div>
            </div>
          </div>
        </motion.div>
      </main>
    </div>
  );
}
