import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  FileText,
  MessageCircle,
  PieChart as PieChartIcon,
  Users,
  UserCheck,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AdminLayout from "@/components/admin/AdminLayout";
import StatCard from "@/components/admin/StatCard";
import ChartCard from "@/components/admin/ChartCard";
import DataTable from "@/components/admin/DataTable";
import { getAdminDocuments, getAdminStats } from "@/services/adminService";
import { api } from "@/lib/api";

const COLORS = ["#6366F1", "#10B981", "#F59E0B", "#EF4444", "#06B6D4", "#8B5CF6"];

export default function AdminDashboardPage() {
  const [overview, setOverview] = useState(null);
  const [recentDocs, setRecentDocs] = useState([]);
  const [qaGroups, setQaGroups] = useState([]);
  const [authorGroups, setAuthorGroups] = useState([]);
  const [topicGroups, setTopicGroups] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function loadAll() {
    setLoading(true);
    setError("");
    try {
      const [ov, docs, qa, authors, topics] = await Promise.all([
        getAdminStats(),
        getAdminDocuments({ page: 1, page_size: 10 }),
        api.analytics("year"),
        api.analytics("author"),
        api.analytics("topic"),
      ]);
      setOverview(ov || {});
      setRecentDocs(docs?.items || []);
      setQaGroups(qa?.groups || []);
      setAuthorGroups((authors?.groups || []).slice(0, 10));
      setTopicGroups((topics?.groups || []).slice(0, 10));
    } catch (e) {
      setError("Không thể tải dữ liệu thống kê. Vui lòng thử lại.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  const k = overview?.kpis || {};
  const successRate = Number(k.total_documents || 0) > 0
    ? ((Number(k.processed || 0) / Number(k.total_documents || 0)) * 100).toFixed(1)
    : "0.0";

  const docsByType = (overview?.documents_by_type || []).map((x) => ({
    name: String(x.value || "other").toUpperCase(),
    value: Number(x.count || 0),
  }));
  const docsStatus = [
    { name: "Uploaded", value: Number(k.uploaded || 0), color: "#3B82F6" },
    { name: "Processing", value: Number(k.processing || 0), color: "#F59E0B" },
    { name: "Processed", value: Number(k.processed || 0), color: "#10B981" },
    { name: "Failed", value: Number(k.failed || 0), color: "#EF4444" },
  ];
  const qaByDay = qaGroups.map((item) => ({ day: String(item.value), count: Number(item.count || 0) }));
  const recSource = (overview?.recent_documents || []).reduce((acc, doc) => {
    const key = String(doc?.file_type || "unknown").toLowerCase();
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const recSourceData = Object.entries(recSource).map(([name, value]) => ({ name, value }));

  const statCards = useMemo(
    () => [
      { title: "Tổng số người dùng", value: Number(k.total_users || 0), icon: Users },
      { title: "Người dùng hoạt động", value: Number(k.active_users || 0), icon: UserCheck },
      { title: "Tổng số tài liệu", value: Number(k.total_documents || 0), icon: FileText },
      { title: "Tổng số câu hỏi Q&A", value: Number(k.total_qa || 0), icon: MessageCircle },
      { title: "Tổng số bản tóm tắt", value: Number(k.total_summaries || 0), icon: Bot },
      { title: "Tổng số khuyến nghị", value: Number(overview?.recent_documents?.length || 0), icon: PieChartIcon },
      { title: "Tỷ lệ xử lý thành công", value: `${successRate}%`, icon: Activity },
      { title: "Thời gian phản hồi TB", value: "~2.1s", icon: Activity },
    ],
    [k, overview, successRate],
  );

  return (
    <AdminLayout
      title="Thống kê hệ thống"
      description="Tổng quan hoạt động của hệ thống Paper RAG KG"
      onRefresh={loadAll}
    >
      {error ? <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">{error}</div> : null}
      {loading ? <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">Đang tải dữ liệu...</div> : null}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {statCards.map((item) => (
          <StatCard key={item.title} title={item.title} value={item.value} icon={item.icon} />
        ))}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Documents by File Type">
          {docsByType.length ? (
            <ResponsiveContainer width="100%" height={340}>
              <PieChart>
                <Pie data={docsByType} dataKey="value" nameKey="name" innerRadius={70} outerRadius={115}>
                  {docsByType.map((entry, index) => <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />)}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : <div className="flex h-[340px] items-center justify-center text-sm text-slate-500">Chưa có dữ liệu.</div>}
        </ChartCard>

        <ChartCard title="Documents Status Overview">
          <ResponsiveContainer width="100%" height={340}>
            <BarChart data={docsStatus}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                {docsStatus.map((x) => <Cell key={x.name} fill={x.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Top Authors">
          {authorGroups.length ? (
            <ResponsiveContainer width="100%" height={340}>
              <BarChart data={authorGroups} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" allowDecimals={false} />
                <YAxis dataKey="name" type="category" width={180} />
                <Tooltip />
                <Legend />
                <Bar dataKey="count" fill="#8B5CF6" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="flex h-[340px] items-center justify-center text-sm text-slate-500">Chưa có dữ liệu.</div>}
        </ChartCard>

        <ChartCard title="Top Topics">
          {topicGroups.length ? (
            <ResponsiveContainer width="100%" height={340}>
              <BarChart data={topicGroups} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" allowDecimals={false} />
                <YAxis dataKey="value" type="category" width={180} />
                <Tooltip />
                <Legend />
                <Bar dataKey="count" fill="#06B6D4" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="flex h-[340px] items-center justify-center text-sm text-slate-500">Chưa có dữ liệu chủ đề.</div>}
        </ChartCard>

        <ChartCard title="QA Activity by Day">
          <ResponsiveContainer width="100%" height={340}>
            <LineChart data={qaByDay}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="count" stroke="#6366F1" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Recommendation Source Distribution">
          {recSourceData.length ? (
            <ResponsiveContainer width="100%" height={340}>
              <PieChart>
                <Pie data={recSourceData} dataKey="value" nameKey="name" innerRadius={70} outerRadius={115}>
                  {recSourceData.map((entry, index) => <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />)}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : <div className="flex h-[340px] items-center justify-center text-sm text-slate-500">Chưa có dữ liệu.</div>}
        </ChartCard>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <DataTable
          columns={[
            { key: "name", label: "Tài liệu" },
            { key: "type", label: "Loại file" },
            { key: "status", label: "Trạng thái" },
            { key: "created", label: "Ngày tạo" },
          ]}
        >
          <tbody>
            {recentDocs.map((doc) => (
              <tr key={doc.id} className="border-t border-slate-200">
                <td className="max-w-[260px] truncate px-3 py-2">{doc.filename || "-"}</td>
                <td className="px-3 py-2">{doc.file_type || "-"}</td>
                <td className="px-3 py-2">{doc.status || "-"}</td>
                <td className="px-3 py-2">{doc.created_at ? new Date(doc.created_at).toLocaleString("vi-VN") : "-"}</td>
              </tr>
            ))}
            {!recentDocs.length ? <tr><td className="px-3 py-3 text-slate-500" colSpan={4}>Chưa có tài liệu gần đây.</td></tr> : null}
          </tbody>
        </DataTable>
        <DataTable
          columns={[
            { key: "question", label: "Câu hỏi gần đây" },
            { key: "doc", label: "Tài liệu" },
            { key: "time", label: "Thời gian" },
          ]}
        >
          <tbody>
            <tr>
              <td colSpan={3} className="px-3 py-3 text-slate-500">
                API câu hỏi gần đây chưa có endpoint admin riêng. TODO: map từ `/qa_history` admin endpoint.
              </td>
            </tr>
          </tbody>
        </DataTable>
      </section>
    </AdminLayout>
  );
}
