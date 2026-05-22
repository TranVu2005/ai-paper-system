import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, Brain, FileText, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { documents } from "@/lib/mockData";
import { api } from "@/lib/api";

export default function SearchPage() {
  const [query, setQuery] = useState("RAG tiếng Việt");
  const [apiResults, setApiResults] = useState(null);
  const [message, setMessage] = useState("");

  const results = useMemo(() => {
    const text = query.toLowerCase();
    return documents
      .map((doc) => ({
        ...doc,
        score: doc.title.toLowerCase().includes(text) ? 0.96 : doc.topics.join(" ").toLowerCase().includes(text) ? 0.88 : 0.72,
      }))
      .filter((doc) => [doc.title, doc.abstract, ...doc.topics, ...doc.methods].join(" ").toLowerCase().includes(text.split(" ")[0] || ""))
      .sort((a, b) => b.score - a.score);
  }, [query]);

  const visibleResults = apiResults || results;

  async function handleSearch() {
    setMessage("");
    try {
      const response = await api.search(query, 10);
      if (response.items?.length) {
        setApiResults(
          response.items.map((item) => ({
            id: item.document_id,
            title: `Tài liệu #${item.document_id}`,
            abstract: item.content,
            topics: [`Chunk ${item.chunk_index}`],
            score: item.score ?? "N/A",
          })),
        );
      } else {
        setMessage("Không có kết quả phù hợp từ backend.");
        setApiResults(null);
      }
    } catch (err) {
      setMessage(err.message || "Không tìm kiếm được. Tạm thời hiển thị dữ liệu mẫu.");
      setApiResults(null);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
          <div>
            <h1 className="text-xl font-semibold">Tìm kiếm RAG</h1>
            <p className="text-sm text-zinc-500">Frontend gửi query đến backend, kết quả semantic trả về theo chunk và điểm liên quan.</p>
          </div>
          <Badge>Semantic Search</Badge>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8 lg:px-8">
        <Card className="rounded-lg border-zinc-200 shadow-sm">
          <CardHeader>
            <CardTitle>Truy vấn tài liệu</CardTitle>
            <CardDescription>Hỗ trợ keyword, semantic search hoặc hybrid search trên document_chunks.embedding.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-3 lg:flex-row">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-3 h-4 w-4 text-zinc-400" />
                <Input className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ví dụ: bài báo về RAG và Knowledge Graph" />
              </div>
              <Button onClick={handleSearch}><Search className="mr-2 h-4 w-4" />Tìm kiếm</Button>
            </div>
            {message && <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{message}</div>}
          </CardContent>
        </Card>

        <section className="mt-6 grid gap-6 xl:grid-cols-[1fr_360px]">
          <Card className="rounded-lg border-zinc-200 shadow-sm">
            <CardHeader>
              <CardTitle>Kết quả</CardTitle>
              <CardDescription>{visibleResults.length} tài liệu phù hợp với truy vấn hiện tại.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {visibleResults.map((doc) => (
                <div key={doc.id} className="rounded-lg border border-zinc-200 p-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <FileText className="h-4 w-4 text-zinc-700" />
                        <p className="font-medium">{doc.title}</p>
                      </div>
                      <p className="mt-2 text-sm leading-6 text-zinc-600">{doc.abstract}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {doc.topics.map((topic) => <Badge key={topic} variant="secondary">{topic}</Badge>)}
                      </div>
                    </div>
                    <Badge>Score {doc.score}</Badge>
                  </div>
                  <div className="mt-4 flex gap-2">
                    <Link to={`/document/${doc.id}`}><Button variant="outline"><ArrowUpRight className="mr-2 h-4 w-4" />Mở tài liệu</Button></Link>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="rounded-lg border-zinc-200 shadow-sm">
            <CardHeader>
              <CardTitle>Luồng tìm kiếm</CardTitle>
              <CardDescription>Vai trò frontend và backend AI.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {[
                ["Frontend", "Gửi query, limit và bộ lọc người dùng."],
                ["Backend", "Kiểm tra quyền truy cập tài liệu và nạp artifact JSON."],
                ["AI module", "Tạo embedding query, truy xuất vector semantic, sắp xếp kết quả."],
                ["Kết quả", "Trả chunk, score, document_id và nguồn để mở tài liệu."],
              ].map(([title, desc]) => (
                <div key={title} className="rounded-lg border border-zinc-200 p-4">
                  <div className="flex items-center gap-2">
                    <Brain className="h-4 w-4 text-zinc-700" />
                    <p className="font-medium">{title}</p>
                  </div>
                  <p className="mt-2 text-sm text-zinc-500">{desc}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </section>
      </main>
    </div>
  );
}
