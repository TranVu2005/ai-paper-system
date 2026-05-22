import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  BookOpen,
  FileText,
  Loader2,
  MoreVertical,
  PanelLeft,
  PanelRight,
  Plus,
  Search,
  Send,
  Trash2,
  User,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import AssistantMessage from "@/components/chat/AssistantMessage";
import DeleteDocumentDialog from "@/components/cms/DeleteDocumentDialog";
import SummaryRenderer from "@/components/summary/SummaryRenderer";
import { cleanAnswerText } from "@/utils/answerCleaner";
import { api } from "@/lib/api";
import { mapApiDocument, mapApiDocuments } from "@/lib/documentMapper";
import { deleteDocument } from "@/services/cmsService";

const statusLabel = {
  uploaded: "Mới tải lên",
  processing: "Đang xử lý",
  building_graph: "Đang xây dựng đồ thị",
  processed: "Sẵn sàng",
  parsed: "Đã parse JSON",
  indexed: "Sẵn sàng",
  failed: "Lỗi xử lý",
};

const DEFAULT_LAYOUT = { left: 26, center: 44, right: 30 };
const LAYOUT_STORAGE_KEY = "document_detail_layout_v1";
const FILE_EXT_RE = /\.(pdf|doc|docx|txt|rtf|md)$/i;

function NotebookLogo() {
  return (
    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-black text-white">
      <FileText className="h-6 w-6" />
    </div>
  );
}

function SourceItem({ doc, active, onSelect, onDelete }) {
  return (
    <div
      className={`group block rounded-lg border p-3 transition hover:border-zinc-500 ${
        active ? "border-zinc-900 bg-zinc-50" : "border-zinc-200 bg-white"
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-[#eef1fb] text-violet-700">
          <FileText className="h-4 w-4" />
        </div>
        <button type="button" onClick={() => onSelect(doc)} className="min-w-0 flex-1 text-left">
          <p className="truncate text-sm font-medium text-zinc-900">{doc.title}</p>
          <p className="mt-1 text-xs text-zinc-500">{doc.type || "FILE"} · {statusLabel[doc.status] || doc.status}</p>
        </button>
        <button
          type="button"
          onClick={() => onDelete(doc)}
          className="opacity-0 transition group-hover:opacity-100 rounded p-1 text-red-500 hover:bg-red-50"
          aria-label="Xóa tài liệu"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function mapQaRow(item) {
  return {
    id: item.id || `${item.created_at || Date.now()}-${Math.random()}`,
    question: item.question,
    answer: cleanAnswerText(item.answer),
    createdAt: item.created_at || null,
    pending: false,
    status: "done",
  };
}

function shouldRenameWorkspaceFromFirstDocument(currentTitle, firstDocumentTitle) {
  const current = String(currentTitle || "").trim();
  const first = String(firstDocumentTitle || "").trim();
  if (!first) return false;
  if (!current) return true;
  if (current.toLowerCase() === "untitled notebook") return true;
  if (FILE_EXT_RE.test(current)) return true;
  return false;
}

export default function DocumentDetailPage() {
  const { id, workspaceId } = useParams();
  const navigate = useNavigate();
  const emptyDocument = useMemo(
    () => ({
      id: null,
      title: "Untitled notebook",
      filename: "Chưa có nguồn",
      abstract: "Thêm nguồn ở cột trái để bắt đầu hỏi đáp và tóm tắt.",
      status: "uploaded",
      authors: [],
      topics: [],
      updated: "",
    }),
    [],
  );

  const [document, setDocument] = useState(emptyDocument);
  const [workspace, setWorkspace] = useState(null);
  const [sources, setSources] = useState([]);
  const [qaItems, setQaItems] = useState([]);
  const [summary, setSummary] = useState(null);
  const [summaryByStyle, setSummaryByStyle] = useState({});
  const [summaryError, setSummaryError] = useState("");
  const [summaryStyle, setSummaryStyle] = useState("academic");
  const [recommendations, setRecommendations] = useState([]);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [recommendationError, setRecommendationError] = useState("");
  const [question, setQuestion] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [recommendationMode, setRecommendationMode] = useState("author");
  const [systemMessages, setSystemMessages] = useState([]);
  const [deletingDocument, setDeletingDocument] = useState(false);
  const [deleteDocumentTarget, setDeleteDocumentTarget] = useState(null);
  const [panelSizes, setPanelSizes] = useState(() => {
    try {
      const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
      if (!raw) return DEFAULT_LAYOUT;
      const parsed = JSON.parse(raw);
      if (
        Number.isFinite(parsed?.left) &&
        Number.isFinite(parsed?.center) &&
        Number.isFinite(parsed?.right)
      ) {
        return parsed;
      }
      return DEFAULT_LAYOUT;
    } catch {
      return DEFAULT_LAYOUT;
    }
  });
  const qaEndRef = useRef(null);
  const layoutRef = useRef(null);
  const dragStateRef = useRef(null);
  const activePollersRef = useRef(new Set());
  const notifiedReadyRef = useRef(new Set());
  const notifiedFailedRef = useRef(new Set());
  const notifiedParsedRef = useRef(new Set());
  const notifiedGraphRef = useRef(new Set());

  useEffect(() => {
    loadWorkspace();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, workspaceId]);

  useEffect(() => {
    return () => {
      activePollersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    qaEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [qaItems.length]);

  useEffect(() => {
    if (!document?.id) return;
    loadDocumentFeatures(document.id, summaryStyle, { includeRecommendations: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summaryStyle, document?.id]);

  useEffect(() => {
    localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(panelSizes));
  }, [panelSizes]);

  useEffect(() => {
    const onMouseMove = (event) => {
      const drag = dragStateRef.current;
      const container = layoutRef.current;
      if (!drag || !container) return;
      const rect = container.getBoundingClientRect();
      if (!rect.width) return;
      const totalPx = rect.width;
      const deltaPx = event.clientX - drag.startX;
      const deltaPercent = (deltaPx / totalPx) * 100;

      const minLeft = (300 / totalPx) * 100;
      const minCenter = (460 / totalPx) * 100;
      const minRight = (340 / totalPx) * 100;

      if (drag.divider === "left") {
        let nextLeft = drag.startSizes.left + deltaPercent;
        let nextCenter = drag.startSizes.center - deltaPercent;
        nextLeft = Math.max(minLeft, nextLeft);
        nextCenter = Math.max(minCenter, nextCenter);
        const adjustedLeft = 100 - drag.startSizes.right - nextCenter;
        const adjustedCenter = 100 - drag.startSizes.right - nextLeft;
        if (nextLeft !== adjustedLeft) nextLeft = adjustedLeft;
        if (nextCenter !== adjustedCenter) nextCenter = adjustedCenter;
        setPanelSizes({
          left: nextLeft,
          center: nextCenter,
          right: drag.startSizes.right,
        });
      } else {
        let nextCenter = drag.startSizes.center + deltaPercent;
        let nextRight = drag.startSizes.right - deltaPercent;
        nextCenter = Math.max(minCenter, nextCenter);
        nextRight = Math.max(minRight, nextRight);
        const adjustedCenter = 100 - drag.startSizes.left - nextRight;
        const adjustedRight = 100 - drag.startSizes.left - nextCenter;
        if (nextCenter !== adjustedCenter) nextCenter = adjustedCenter;
        if (nextRight !== adjustedRight) nextRight = adjustedRight;
        setPanelSizes({
          left: drag.startSizes.left,
          center: nextCenter,
          right: nextRight,
        });
      }
    };

    const onMouseUp = () => {
      dragStateRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  function startResize(divider, event) {
    dragStateRef.current = {
      divider,
      startX: event.clientX,
      startSizes: panelSizes,
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  function pushSystemMessage(content, variant = "info") {
    setSystemMessages((prev) => {
      const next = [...prev, { id: `${Date.now()}-${Math.random()}`, content, variant }];
      return next.slice(-20);
    });
  }

  function isReadyStatus(status) {
    return ["processed", "indexed"].includes(String(status || "").toLowerCase());
  }

  function isSummaryReadyStatus(status) {
    return ["parsed", "processed", "indexed"].includes(String(status || "").toLowerCase());
  }

  function shouldPollStatus(status) {
    return ["uploaded", "processing", "parsed"].includes(String(status || "").toLowerCase());
  }

  async function pollIngestionStatus(documentId, title) {
    if (!documentId || activePollersRef.current.has(documentId)) return;
    activePollersRef.current.add(documentId);
    try {
      let attempts = 0;
      while (attempts < 120) {
        const statusResponse = await api.getDocumentStatus(documentId);
        const currentStatus = String(statusResponse?.status || "").toLowerCase();

        setSources((prev) =>
          prev.map((item) =>
            String(item.id) === String(documentId) ? { ...item, status: currentStatus || item.status } : item,
          ),
        );
        setDocument((prev) =>
          String(prev?.id) === String(documentId) ? { ...prev, status: currentStatus || prev.status } : prev,
        );

        if (isReadyStatus(currentStatus)) {
          if (!notifiedGraphRef.current.has(documentId)) {
            notifiedGraphRef.current.add(documentId);
            pushSystemMessage(`Hệ thống đang xử lý xây dựng đồ thị cho "${title}". Vui lòng chờ trước khi hỏi đáp.`, "info");
          }
          if (!notifiedReadyRef.current.has(documentId)) {
            notifiedReadyRef.current.add(documentId);
            pushSystemMessage(`Tài liệu "${title}" đã xử lý xong. Bạn có thể đặt câu hỏi và dùng AI ngay.`, "success");
          }
          await loadDocumentFeatures(documentId, summaryStyle, { includeRecommendations: true });
          return;
        }

        if (currentStatus === "failed") {
          if (!notifiedFailedRef.current.has(documentId)) {
            notifiedFailedRef.current.add(documentId);
            pushSystemMessage(`Xử lý tài liệu "${title}" thất bại. Vui lòng thử tải lại file.`, "error");
          }
          return;
        }

        if (currentStatus === "parsed" && !notifiedParsedRef.current.has(documentId)) {
          notifiedParsedRef.current.add(documentId);
          pushSystemMessage(`Tài liệu "${title}" đã ingestion xong và tạo JSON thành công.`, "success");
          if (!notifiedGraphRef.current.has(documentId)) {
            notifiedGraphRef.current.add(documentId);
            pushSystemMessage(`Hệ thống đang xử lý xây dựng đồ thị cho "${title}". Vui lòng chờ trước khi hỏi đáp.`, "info");
          }
        }

        if (!shouldPollStatus(currentStatus)) return;
        attempts += 1;
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
    } catch {
      pushSystemMessage(`Không theo dõi được tiến trình xử lý cho "${title}".`, "error");
    } finally {
      activePollersRef.current.delete(documentId);
    }
  }

  useEffect(() => {
    if (!document?.id) return;
    if (shouldPollStatus(document.status)) {
      pollIngestionStatus(document.id, document.title || document.filename || `#${document.id}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document?.id, document?.status]);

  useEffect(() => {
    if (!document?.id) return;
    if (!isReadyStatus(document?.status)) return;
    getRecommendations(document.id).then((items) => setRecommendations(items));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document?.id, document?.status]);

  async function loadWorkspace() {
    setLoading(true);
    setMessage("");
    if (workspaceId) {
      const workspaceResponse = await Promise.allSettled([api.getWorkspace(workspaceId)]);
      if (workspaceResponse[0].status !== "fulfilled") {
        setMessage("Không tải được phiên làm việc. Bạn cần đăng nhập lại hoặc kiểm tra backend.");
        setLoading(false);
        return;
      }

      let nextWorkspace = workspaceResponse[0].value;
      const workspaceDocuments = mapApiDocuments(nextWorkspace.documents || [], []);
      const currentDocument =
        workspaceDocuments.find((item) => String(item.id) === String(document?.id)) ||
        workspaceDocuments[0];

      const firstDocumentTitle = workspaceDocuments[0]?.title;
      if (
        workspaceId &&
        shouldRenameWorkspaceFromFirstDocument(nextWorkspace?.title, firstDocumentTitle)
      ) {
        try {
          await api.updateWorkspace(workspaceId, { title: firstDocumentTitle });
          nextWorkspace = { ...nextWorkspace, title: firstDocumentTitle };
        } catch {
          // Keep current workspace title if rename fails.
        }
      }

      setWorkspace(nextWorkspace);
      setSources(workspaceDocuments);

      if (!currentDocument) {
        setDocument({
          ...emptyDocument,
          id: null,
          title: nextWorkspace.title || "Untitled notebook",
        });
        setQaItems([]);
        setSummary(null);
        setSummaryByStyle({});
        setRecommendations([]);
        setLoading(false);
        return;
      }

      setDocument(currentDocument);
      setLoading(false);
      loadDocumentFeatures(currentDocument.id, summaryStyle);
      return;
    }

    const [docsResponse, docResponse] =
      await Promise.allSettled([
        api.listDocuments({ page_size: 50 }),
        api.getDocument(id),
      ]);

    if (docsResponse.status === "fulfilled") setSources(mapApiDocuments(docsResponse.value, []));
    if (docResponse.status === "fulfilled") {
      const nextDoc = mapApiDocument(docResponse.value);
      setDocument(nextDoc);
      loadDocumentFeatures(nextDoc.id, summaryStyle);
    }

    if (docResponse.status === "rejected") {
      setMessage("Không tải được tài liệu. Bạn cần đăng nhập lại hoặc kiểm tra backend.");
    }
    setLoading(false);
  }

  async function getRecommendations(documentId) {
    setRecommendationLoading(true);
    setRecommendationError("");
    try {
      const response = await api.getRecommendations(documentId);
      return response?.items || [];
    } catch (error) {
      console.error("getRecommendations failed", error);
      setRecommendationError("Không thể tải gợi ý tài liệu. Vui lòng thử lại sau.");
      return [];
    } finally {
      setRecommendationLoading(false);
    }
  }

  async function loadDocumentFeatures(documentId, style = "academic", options = {}) {
    const { includeRecommendations = true } = options;
    let qaRows = [];
    const [qaResponse, summaryResponse] = await Promise.allSettled([
      api.getQaHistory(documentId),
      api.getSummary(documentId, style),
    ]);

    if (qaResponse.status === "fulfilled" && qaResponse.value?.length) {
      const rows = qaResponse.value.map(mapQaRow);
      rows.sort((a, b) => {
        const ta = a.createdAt ? new Date(a.createdAt).getTime() : 0;
        const tb = b.createdAt ? new Date(b.createdAt).getTime() : 0;
        return ta - tb;
      });
      qaRows = rows;
      setQaItems(rows);
    } else {
      qaRows = [];
      setQaItems([]);
    }
    let summaryValue = summaryResponse.status === "fulfilled" ? summaryResponse.value : null;
    let summaryFetchError = summaryResponse.status === "rejected";
    const summaryText = String(summaryValue?.summary || "").trim();

    setSummaryError(summaryFetchError ? "Không thể tải nội dung tóm tắt." : "");
    if (summaryValue && summaryText) {
      setSummary(summaryValue);
      const styleKey = String(summaryValue?.summary_type || style || "academic").toLowerCase();
      setSummaryByStyle((prev) => ({ ...prev, [styleKey]: summaryValue }));
    }
    if (includeRecommendations) {
      getRecommendations(documentId).then((items) => setRecommendations(items));
    }
    return { summary: summaryValue, qaRows };
  }

  async function handleUploadSource(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy("upload");
    setMessage("");
    try {
      const response = await api.uploadDocument(file, workspaceId || null);
      setMessage("Đã tải nguồn mới lên backend.");
      if (response?.document_id) {
        const displayName = file?.name || `#${response.document_id}`;
        pushSystemMessage(`Đã tải "${displayName}". Hệ thống đang xử lý tài liệu, vui lòng đợi trong giây lát...`, "info");
        pollIngestionStatus(response.document_id, displayName);
      }
      if (response.document_id) {
        if (workspaceId) {
          await loadWorkspace();
        } else {
          navigate(`/document/${response.document_id}`);
        }
      }
    } catch (err) {
      setMessage(err.message || "Không tải được nguồn.");
    } finally {
      setBusy("");
      event.target.value = "";
    }
  }

  async function handleRequestSummary(level, summaryStyle = "academic") {
    if (!document.id) return;
    if (!isSummaryReadyStatus(document.status)) {
      setMessage("Tài liệu đang ingestion. Vui lòng đợi hoàn tất rồi tạo tóm tắt.");
      return;
    }
    const style = String(summaryStyle || "academic").toLowerCase();
    setSummaryStyle(style);
    setBusy(`summary-${style}`);
    try {
      const response = await api.requestSummaryByStyle(document.id, style, level);
      const immediateText = String(response?.summary_text || response?.summary || "").trim();
      if (immediateText) {
        const nextSummary = {
          document_id: document.id,
          summary: immediateText,
          created_at: new Date().toISOString(),
        };
        setSummary(nextSummary);
        setSummaryByStyle((prev) => ({ ...prev, [style]: nextSummary }));
      }
      const features = await loadDocumentFeatures(document.id, style, { includeRecommendations: false });
      if (!features?.summary && response?.job_id) {
        try {
          const job = await api.getJobStatus(response.job_id);
          const text = String(job?.result?.summary_text || job?.result?.summary_preview || "").trim();
          if (text) {
            const nextSummary = {
              document_id: document.id,
              summary: text,
              created_at: new Date().toISOString(),
            };
            setSummary(nextSummary);
            setSummaryByStyle((prev) => ({ ...prev, [style]: nextSummary }));
          }
        } catch {
          // keep current UI message if fallback fetch fails
        }
      }
      setMessage(
        response.status === "done"
          ? `Đã tạo tóm tắt (${style}) xong.`
          : response.status === "failed"
            ? response.message || "Không tạo được tóm tắt."
            : "Đã nhận yêu cầu tóm tắt.",
      );
      // Force one more fetch to avoid stale UI after generation.
      await loadDocumentFeatures(document.id, style, { includeRecommendations: false });
    } catch (err) {
      setMessage(err.message || "Không tạo được tóm tắt.");
    } finally {
      setBusy("");
    }
  }

  async function handleAskQuestion() {
    const cleanQuestion = question.trim();
    if (!cleanQuestion) return;
    if (!document.id) return;
    if (!isReadyStatus(document.status)) {
      setMessage("Hệ thống đang xử lý xây dựng đồ thị. Vui lòng đợi tài liệu ở trạng thái Sẵn sàng rồi hỏi đáp.");
      return;
    }
    const optimisticId = crypto.randomUUID();
    setQuestion("");
    setBusy("qa");
    const optimisticItem = {
      id: optimisticId,
      question: cleanQuestion,
      answer: "AI đang xử lý câu trả lời...",
      createdAt: new Date().toISOString(),
      pending: true,
      status: "loading",
    };
    setQaItems((prev) => [...prev, optimisticItem]);
    try {
      const response = await api.requestQuestion(document.id, cleanQuestion);
      if (response?.job_id && response.status !== "done" && response.status !== "failed") {
        const started = Date.now();
        let finished = false;
        while (!finished && Date.now() - started < 180000) {
          await new Promise((resolve) => setTimeout(resolve, 2000));
          const job = await api.getJobStatus(response.job_id);
          if (job?.status === "done") {
            finished = true;
            const features = await loadDocumentFeatures(document.id, summaryStyle, { includeRecommendations: false });
            const latestAnswer = features?.qaRows?.length
              ? features.qaRows[features.qaRows.length - 1]?.answer
              : "";
            setQaItems((prev) =>
              prev.map((item) => (item.id === optimisticId
                ? {
                    ...item,
                    answer: latestAnswer || "Không có câu trả lời.",
                    pending: false,
                    status: "done",
                  }
                : item)),
            );
            setMessage("Đã trả lời câu hỏi.");
          } else if (job?.status === "failed") {
            finished = true;
            setQaItems((prev) =>
              prev.map((item) => (item.id === optimisticId
                ? {
                    ...item,
                    answer: "Không thể trả lời câu hỏi lúc này. Vui lòng thử lại.",
                    pending: false,
                    status: "error",
                  }
                : item)),
            );
            setMessage(job?.error_message || "Không trả lời được câu hỏi.");
          }
        }
        if (!finished) {
          setQaItems((prev) =>
            prev.map((item) => (item.id === optimisticId
              ? {
                  ...item,
                  answer: "Không thể trả lời câu hỏi lúc này. Vui lòng thử lại.",
                  pending: false,
                  status: "error",
                }
              : item)),
          );
          setMessage("Câu hỏi đang được xử lý, vui lòng đợi thêm.");
        }
      } else {
        const features = await loadDocumentFeatures(document.id, summaryStyle, { includeRecommendations: false });
        const latestAnswer = features?.qaRows?.length
          ? features.qaRows[features.qaRows.length - 1]?.answer
          : "";
        setQaItems((prev) =>
          prev.map((item) => (item.id === optimisticId
            ? {
                ...item,
                answer: latestAnswer || "Không có câu trả lời.",
                pending: false,
                status: response.status === "failed" ? "error" : "done",
              }
            : item)),
        );
        setMessage(
          response.status === "done"
            ? "Đã trả lời câu hỏi."
            : response.status === "failed"
              ? response.message || "Không trả lời được câu hỏi."
            : "Đã nhận câu hỏi.",
        );
      }
    } catch (err) {
      setQaItems((prev) =>
        prev.map((item) => (item.id === optimisticId
          ? {
              ...item,
              answer: "Không thể trả lời câu hỏi lúc này. Vui lòng thử lại.",
              pending: false,
              status: "error",
            }
          : item)),
      );
      setMessage(err.message || "Không gửi được câu hỏi.");
    } finally {
      setBusy("");
    }
  }

  async function handleDeleteSource() {
    const target = deleteDocumentTarget;
    if (!target?.id) return;
    setDeletingDocument(true);
    try {
      await deleteDocument(target.id);
      const nextSources = sources.filter((item) => String(item.id) !== String(target.id));
      setSources(nextSources);
      if (String(document?.id) === String(target.id)) {
        const fallback = nextSources[0] || null;
        if (fallback) {
          setDocument(fallback);
          await loadDocumentFeatures(fallback.id, summaryStyle);
        } else {
          setDocument({ ...emptyDocument, id: null, title: workspace?.title || "Untitled notebook" });
          setQaItems([]);
          setRecommendations([]);
          setSummary(null);
          setSummaryByStyle({});
        }
      }
      setRecommendations([]);
      setMessage("Đã xóa tài liệu.");
      setDeleteDocumentTarget(null);
    } catch (err) {
      setMessage(err.message || "Không thể xóa tài liệu. Vui lòng thử lại.");
    } finally {
      setDeletingDocument(false);
    }
  }

  const filteredSources = sources.filter((doc) =>
    `${doc.title} ${doc.filename} ${doc.topics?.join(" ")}`.toLowerCase().includes(sourceQuery.toLowerCase()),
  );
  const groupedRecommendations = useMemo(() => {
    const byAuthor = recommendations.filter((r) => String(r.recommendation_type || "").toLowerCase() === "author");
    const byMethod = recommendations.filter((r) => String(r.recommendation_type || "").toLowerCase() === "method");
    return { author: byAuthor, method: byMethod };
  }, [recommendations]);
  const activeRecommendations = groupedRecommendations[recommendationMode] || [];
  const isDocumentReady = isReadyStatus(document?.status);
  const sourceCount = sources.length;
  const notebookTitle = workspace?.title || document.title || "Untitled notebook";
  const currentSummary = summaryByStyle[summaryStyle] || summary;
  const summaryBusy = busy.startsWith("summary-");
  const summaryText = currentSummary?.summary || "";

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#eef1fb] text-zinc-700">
        <div className="flex items-center gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Đang tải notebook...</span>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="min-h-screen bg-[#eef1fb] text-zinc-950">
      <header className="flex h-[84px] items-center justify-between px-6">
        <div className="flex min-w-0 items-center gap-5">
          <Link to="/home" aria-label="Về trang sổ ghi chú">
            <NotebookLogo />
          </Link>
          <h1 className="truncate text-2xl font-medium">{notebookTitle}</h1>
          <Badge variant="secondary">{statusLabel[document.status] || document.status}</Badge>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            className="hidden rounded-full xl:inline-flex"
            onClick={() => setPanelSizes(DEFAULT_LAYOUT)}
          >
            Reset layout
          </Button>
          <label className="hidden h-10 cursor-pointer items-center rounded-full bg-black px-6 text-sm font-medium text-white hover:bg-zinc-800 md:inline-flex">
            <Plus className="mr-2 h-5 w-5" />
            Thêm nguồn
            <input
              type="file"
              accept=".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              className="hidden"
              onChange={handleUploadSource}
            />
          </label>
          <Link to="/profile" className="flex h-11 w-11 items-center justify-center rounded-full bg-violet-600 text-white">
            <User className="h-5 w-5" />
          </Link>
        </div>
      </header>

      <main
        ref={layoutRef}
        className="grid min-h-[calc(100vh-84px)] gap-5 px-5 pb-5 xl:flex xl:h-[calc(100vh-84px)] xl:gap-3"
      >
        <aside
          style={{ "--panel-left": `${panelSizes.left}%` }}
          className="flex flex-col rounded-lg bg-white xl:min-h-0 xl:shrink-0 xl:basis-[var(--panel-left)] xl:overflow-hidden"
        >
          <div className="flex h-16 shrink-0 items-center justify-between border-b border-zinc-200 px-5">
            <h2 className="text-lg font-medium">Nguồn tài liệu</h2>
            <PanelLeft className="h-5 w-5 text-zinc-600" />
          </div>

          <div className="flex-1 p-5 xl:min-h-0 xl:overflow-y-auto">
            <label className="flex h-12 w-full cursor-pointer items-center justify-center rounded-full border border-zinc-300 bg-white text-sm font-medium hover:border-zinc-600">
              {busy === "upload" ? <Loader2 className="mr-3 h-5 w-5 animate-spin" /> : <Plus className="mr-3 h-5 w-5" />}
              Thêm nguồn
              <input
                type="file"
                accept=".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                className="hidden"
                onChange={handleUploadSource}
              />
            </label>

            <div className="mt-5 rounded-2xl border border-zinc-200 bg-zinc-50 p-4">
              <div className="relative">
                <Search className="absolute left-3 top-3 h-5 w-5 text-zinc-500" />
                <input
                  value={sourceQuery}
                  onChange={(event) => setSourceQuery(event.target.value)}
                  className="h-11 w-full bg-transparent pl-10 text-base outline-none"
                  placeholder="Tìm trong nguồn đã tải"
                />
              </div>
            </div>

            <div className="mt-5 space-y-3">
              {filteredSources.map((doc) => (
                <SourceItem
                  key={doc.id}
                  doc={doc}
                  active={String(doc.id) === String(document.id)}
                  onSelect={(nextDocument) => {
                    setDocument(nextDocument);
                    loadDocumentFeatures(nextDocument.id, summaryStyle);
                  }}
                  onDelete={(targetDoc) => setDeleteDocumentTarget(targetDoc)}
                />
              ))}
            </div>

            <div className="mt-6 border-t border-zinc-200 pt-5">
              <div className="flex items-center gap-2">
                <BookOpen className="h-4 w-4 text-zinc-700" />
                <h3 className="font-medium">Gợi ý tài liệu</h3>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setRecommendationMode("author")}
                  className={`rounded-full px-3 py-2 text-sm font-medium transition ${
                    recommendationMode === "author"
                      ? "bg-zinc-900 text-white"
                      : "border border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400"
                  }`}
                >
                  Theo tác giả
                </button>
                <button
                  type="button"
                  onClick={() => setRecommendationMode("method")}
                  className={`rounded-full px-3 py-2 text-sm font-medium transition ${
                    recommendationMode === "method"
                      ? "bg-zinc-900 text-white"
                      : "border border-zinc-200 bg-white text-zinc-700 hover:border-zinc-400"
                  }`}
                >
                  Theo method
                </button>
              </div>
              <div className="mt-3 space-y-3">
                {recommendationLoading ? (
                  <div className="rounded-lg border border-dashed border-zinc-300 p-4 text-sm leading-6 text-zinc-500">
                    Đang tải gợi ý tài liệu...
                  </div>
                ) : !isDocumentReady ? (
                  <div className="rounded-lg border border-dashed border-zinc-300 p-4 text-sm leading-6 text-zinc-500">
                    Hệ thống đang xử lý tài liệu. Gợi ý sẽ xuất hiện sau khi xử lý xong.
                  </div>
                ) : recommendationError ? (
                  <div className="rounded-lg border border-dashed border-zinc-300 p-4 text-sm leading-6 text-zinc-500">
                    {recommendationError}
                  </div>
                ) : activeRecommendations.length ? activeRecommendations.map((item) => (
                  <div key={item.id || item.title} className="rounded-lg bg-zinc-50 p-3">
                    <p className="text-sm font-medium text-zinc-900">{item.title}</p>
                    <p className="mt-1 text-xs leading-5 text-zinc-500">{item.reason || "Không có lý do gợi ý."}</p>
                    <p className="mt-1 text-xs text-zinc-600">
                      Score: {Number.isFinite(Number(item.score)) ? Number(item.score).toFixed(2) : "0.00"}
                    </p>
                    {item.external_url ? (
                      <a
                        href={item.external_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-1 inline-block text-xs text-blue-600 hover:underline"
                      >
                        Xem nguồn
                      </a>
                    ) : null}
                  </div>
                )) : (
                  <div className="rounded-lg border border-dashed border-zinc-300 p-4 text-sm leading-6 text-zinc-500">
                    {recommendations.length
                      ? `Chưa có gợi ý ${recommendationMode === "author" ? "theo tác giả" : "theo method"}.`
                      : "Chưa tìm thấy tài liệu liên quan để gợi ý."}
                  </div>
                )}
              </div>
            </div>
          </div>
        </aside>

        <div
          className="hidden xl:flex w-2 cursor-col-resize items-center justify-center rounded-full bg-transparent hover:bg-indigo-100"
          onMouseDown={(event) => startResize("left", event)}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize source and chat panels"
        >
          <div className="h-16 w-[2px] rounded-full bg-zinc-300" />
        </div>

        <section
          style={{ "--panel-center": `${panelSizes.center}%` }}
          className="flex flex-col rounded-lg bg-white xl:min-h-0 xl:shrink-0 xl:basis-[var(--panel-center)] xl:overflow-hidden"
        >
          <div className="flex h-16 shrink-0 items-center justify-between border-b border-zinc-200 px-5">
            <h2 className="text-lg font-medium">Cuộc trò chuyện</h2>
            <MoreVertical className="h-5 w-5 text-zinc-600" />
          </div>

          <div className="flex-1 px-7 py-8 xl:min-h-0 xl:overflow-y-auto">
            {message && <div className="mb-5 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{message}</div>}
            <div className="mx-auto max-w-3xl">
              <div className="flex h-16 w-16 items-center justify-center rounded-lg bg-[#eef1fb] text-violet-700">
                <BookOpen className="h-8 w-8" />
              </div>
              <h3 className="mt-12 text-4xl font-medium tracking-normal">{notebookTitle}</h3>
              <p className="mt-3 text-lg text-zinc-700">{sourceCount} nguồn · {document.updated || "chưa rõ thời gian"}</p>
              <p className="mt-5 text-sm leading-6 text-zinc-600">
                {document.id ? `Nguồn đang chọn: ${document.title}. ${document.abstract}` : document.abstract}
              </p>

              <div className="mt-10 space-y-4">
                {systemMessages.map((item) => (
                  <div
                    key={item.id}
                    className={`rounded-lg border p-4 text-sm ${
                      item.variant === "success"
                        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                        : item.variant === "error"
                          ? "border-rose-200 bg-rose-50 text-rose-800"
                          : "border-sky-200 bg-sky-50 text-sky-800"
                    }`}
                  >
                    {item.content}
                  </div>
                ))}
                {qaItems.map((item) => (
                  <AssistantMessage key={item.id || `${item.question}-${item.createdAt || ""}`} item={item} />
                ))}
                <div ref={qaEndRef} />
              </div>
            </div>
          </div>

          <div className="shrink-0 px-7 pb-5">
            <div className="mx-auto flex max-w-5xl items-end gap-4 rounded-2xl border border-zinc-400 bg-white p-3 shadow-sm">
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    handleAskQuestion();
                  }
                }}
                rows={2}
                className="min-h-[56px] max-h-32 min-w-0 flex-1 resize-none px-4 py-3 text-base outline-none"
                placeholder="Hỏi về tài liệu này..."
              />
              <span className="hidden text-sm text-zinc-600 sm:inline">
                {document?.id ? "1 nguồn đang chọn" : "Chưa chọn nguồn"}
              </span>
              <Button onClick={handleAskQuestion} disabled={busy === "qa" || !document.id || !isReadyStatus(document.status) || !question.trim()} size="icon" className="h-12 w-12 rounded-full bg-zinc-200 text-zinc-700 hover:bg-zinc-300 disabled:cursor-not-allowed disabled:opacity-60">
                {busy === "qa" ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
              </Button>
            </div>
          </div>
        </section>

        <div
          className="hidden xl:flex w-2 cursor-col-resize items-center justify-center rounded-full bg-transparent hover:bg-indigo-100"
          onMouseDown={(event) => startResize("right", event)}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat and summary panels"
        >
          <div className="h-16 w-[2px] rounded-full bg-zinc-300" />
        </div>

        <aside
          style={{ "--panel-right": `${panelSizes.right}%` }}
          className="flex flex-col rounded-lg bg-white xl:min-h-0 xl:shrink-0 xl:basis-[var(--panel-right)] xl:overflow-hidden"
        >
          <div className="flex h-16 shrink-0 items-center justify-between border-b border-zinc-200 px-5">
            <h2 className="text-lg font-medium">Tóm tắt</h2>
            <PanelRight className="h-5 w-5 text-zinc-600" />
          </div>

          <div className="flex-1 p-5 xl:min-h-0 xl:overflow-y-auto">
            <section className="rounded-2xl border border-zinc-200 bg-zinc-50 p-5">
              <div className="mb-3 flex items-center gap-2 text-zinc-900">
                <Button
                  onClick={() => handleRequestSummary("medium", "academic")}
                  disabled={!document.id || summaryBusy || !isSummaryReadyStatus(document.status)}
                  className="rounded-full bg-zinc-900 px-4 text-sm font-medium text-white hover:bg-zinc-800"
                >
                  {busy === "summary-academic" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Academic"}
                </Button>
                <Button
                  onClick={() => handleRequestSummary("medium", "semantic")}
                  disabled={!document.id || summaryBusy || !isSummaryReadyStatus(document.status)}
                  className="rounded-full bg-zinc-700 px-4 text-sm font-medium text-white hover:bg-zinc-600"
                >
                  {busy === "summary-semantic" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Semantic"}
                </Button>
                <Button
                  onClick={() => handleRequestSummary("medium", "executive")}
                  disabled={!document.id || summaryBusy || !isSummaryReadyStatus(document.status)}
                  className="rounded-full bg-zinc-600 px-4 text-sm font-medium text-white hover:bg-zinc-500"
                >
                  {busy === "summary-executive" ? <Loader2 className="h-4 w-4 animate-spin" /> : "Executive"}
                </Button>
              </div>
              <SummaryRenderer
                text={summaryText}
                style={summaryStyle}
                loading={summaryBusy}
                error={summaryError}
              />
            </section>
          </div>
        </aside>
      </main>
      </div>
      <DeleteDocumentDialog
        open={Boolean(deleteDocumentTarget)}
        loading={deletingDocument}
        onClose={() => !deletingDocument && setDeleteDocumentTarget(null)}
        onConfirm={handleDeleteSource}
      />
    </>
  );
}
