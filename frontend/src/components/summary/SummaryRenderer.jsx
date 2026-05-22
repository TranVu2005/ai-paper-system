import { useMemo, useRef, useState } from "react";
import { Copy, Minimize2, MoveUp, Maximize2 } from "lucide-react";
import SummaryInsight from "./SummaryInsight";
import SummarySection from "./SummarySection";
import { parseSummarySections } from "@/utils/summaryParser";

export default function SummaryRenderer({
  text,
  style = "academic",
  loading = false,
  error = "",
}) {
  const [expandedMap, setExpandedMap] = useState({});
  const listRef = useRef(null);
  const sections = useMemo(() => parseSummarySections(text), [text]);
  const charCount = String(text || "").length;

  const allExpanded = sections.length > 0 && sections.every((section) => expandedMap[section.id] !== false);

  function handleToggleAll(expanded) {
    const next = {};
    sections.forEach((section) => {
      next[section.id] = expanded;
    });
    setExpandedMap(next);
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(String(text || ""));
    } catch {
      // noop
    }
  }

  if (error) {
    return <p className="text-sm text-zinc-600">Chưa có nội dung tóm tắt.</p>;
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((item) => (
          <div key={item} className="h-24 animate-pulse rounded-xl border border-zinc-200 bg-zinc-100" />
        ))}
      </div>
    );
  }

  if (!String(text || "").trim()) {
    return <p className="text-sm text-zinc-600">Chưa có nội dung tóm tắt.</p>;
  }

  return (
    <div className="space-y-3">
      <SummaryInsight style={style} sectionCount={sections.length} charCount={charCount} />
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
        >
          <Copy className="h-3.5 w-3.5" />
          Copy
        </button>
        <button
          type="button"
          onClick={() => handleToggleAll(true)}
          className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
        >
          <Maximize2 className="h-3.5 w-3.5" />
          Expand All
        </button>
        <button
          type="button"
          onClick={() => handleToggleAll(false)}
          className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
        >
          <Minimize2 className="h-3.5 w-3.5" />
          Collapse All
        </button>
        {!allExpanded ? (
          <button
            type="button"
            onClick={() => handleToggleAll(true)}
            className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
          >
            <MoveUp className="h-3.5 w-3.5" />
            Mở lại tất cả
          </button>
        ) : null}
      </div>
      <div ref={listRef} className="max-h-[62vh] space-y-3 overflow-y-auto pr-1 scroll-smooth">
        {sections.map((section) => (
          <SummarySection
            key={section.id}
            section={section}
            style={style}
            expanded={expandedMap[section.id] !== false}
            onToggle={() => setExpandedMap((prev) => ({ ...prev, [section.id]: prev[section.id] === false }))}
          />
        ))}
      </div>
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => listRef.current?.scrollTo({ top: 0, behavior: "smooth" })}
          className="inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
        >
          <MoveUp className="h-3.5 w-3.5" />
          Lên đầu trang
        </button>
      </div>
    </div>
  );
}
