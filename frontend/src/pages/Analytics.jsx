import { useEffect, useState } from "react";
import { BarChart3, ExternalLink, LineChart, PieChart, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { analytics } from "@/lib/mockData";
import { api } from "@/lib/api";

const datasets = {
  year: analytics.byYear,
  topic: analytics.byTopic,
  author: analytics.byAuthor,
};

const title = {
  year: "Thống kê theo năm xuất bản",
  topic: "Thống kê theo chủ đề",
  author: "Thống kê theo tác giả",
};

export default function AnalyticsPage() {
  const [groupBy, setGroupBy] = useState("year");
  const [overview, setOverview] = useState(null);
  const [message, setMessage] = useState("");
  const rows = overview?.groups?.length ? overview.groups : datasets[groupBy];
  const max = Math.max(...rows.map((row) => row.count), 1);

  useEffect(() => {
    async function loadAnalytics() {
      try {
        const response = await api.analytics(groupBy);
        setOverview(response);
        setMessage("");
      } catch {
        setOverview(null);
        setMessage("Đang hiển thị dữ liệu mẫu vì backend analytics chưa sẵn sàng.");
      }
    }
    loadAnalytics();
  }, [groupBy]);

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-950">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
          <div>
            <h1 className="text-xl font-semibold">Thống kê và trực quan hóa</h1>
            <p className="text-sm text-zinc-500">Dashboard xu hướng nghiên cứu theo năm, chủ đề và tác giả.</p>
          </div>
          <Button variant="outline"><ExternalLink className="mr-2 h-4 w-4" />Mở Superset</Button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8 lg:px-8">
        <div className="grid gap-4 md:grid-cols-4">
          {[
            ["Tổng tài liệu", overview?.total_documents ?? "51", BarChart3],
            ["Chủ đề", "18", PieChart],
            ["Tác giả", "27", Users],
            ["Năm dữ liệu", "4", LineChart],
          ].map(([label, value, Icon]) => (
            <div key={label} className="rounded-lg border border-zinc-200 bg-white p-5">
              <div className="flex items-center justify-between">
                <span className="text-sm text-zinc-500">{label}</span>
                <Icon className="h-4 w-4 text-zinc-500" />
              </div>
              <p className="mt-3 text-3xl font-semibold">{value}</p>
            </div>
          ))}
        </div>

        <section className="mt-6 grid gap-6 xl:grid-cols-[1fr_380px]">
          <Card className="rounded-lg border-zinc-200 shadow-sm">
            <CardHeader>
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <CardTitle>{title[groupBy]}</CardTitle>
                  <CardDescription>Dữ liệu ánh xạ với `/api/v1/cms/analytics/overview?group_by={groupBy}`.</CardDescription>
                  {message && <p className="mt-2 text-sm text-amber-700">{message}</p>}
                </div>
                <Tabs value={groupBy} onValueChange={setGroupBy}>
                  <TabsList>
                    <TabsTrigger value="year">Year</TabsTrigger>
                    <TabsTrigger value="topic">Topic</TabsTrigger>
                    <TabsTrigger value="author">Author</TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {rows.map((row) => (
                  <div key={row.value} className="grid gap-2 md:grid-cols-[180px_1fr_60px] md:items-center">
                    <div className="font-medium">{row.value}</div>
                    <div className="h-8 rounded-md bg-zinc-100">
                      <div className="h-8 rounded-md bg-zinc-900" style={{ width: `${(row.count / max) * 100}%` }} />
                    </div>
                    <div className="text-right font-semibold">{row.count}</div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card className="rounded-lg border-zinc-200 shadow-sm">
            <CardHeader>
              <CardTitle>Tích hợp Superset</CardTitle>
              <CardDescription>Frontend có thể nhúng dashboard Superset bằng iframe hoặc link ngoài.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-lg border border-dashed border-zinc-300 bg-zinc-100 p-6 text-center">
                <BarChart3 className="mx-auto h-10 w-10 text-zinc-500" />
                <p className="mt-3 font-medium">Superset Embedded Dashboard</p>
                <p className="mt-2 text-sm text-zinc-500">Đặt URL dashboard vào biến môi trường local nếu cần tích hợp.</p>
              </div>
              {["publication_year", "authors", "topics"].map((field) => (
                <div key={field} className="flex items-center justify-between rounded-lg border border-zinc-200 p-3">
                  <span className="text-sm">{field}</span>
                  <Badge variant="secondary">DocumentMetadata</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </section>
      </main>
    </div>
  );
}
