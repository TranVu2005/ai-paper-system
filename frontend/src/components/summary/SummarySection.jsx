import { ChevronDown, ChevronUp } from "lucide-react";
import { removeDuplicateSectionTitle } from "@/utils/summaryCleaner";

const STYLE_MAP = {
  academic: "border-l-blue-500 bg-blue-50/70",
  semantic: "border-l-violet-500 bg-violet-50/70",
  executive: "border-l-emerald-500 bg-emerald-50/70",
};

function renderContent(content) {
  const lines = String(content || "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return null;

  const allBullets = lines.every((line) => /^[-*•]\s+/.test(line));
  if (allBullets) {
    return (
      <ul className="list-disc space-y-1 pl-5">
        {lines.map((line, idx) => <li key={idx}>{line.replace(/^[-*•]\s+/, "")}</li>)}
      </ul>
    );
  }

  const allNumbered = lines.every((line) => /^\d+[\.\)]\s+/.test(line));
  if (allNumbered) {
    return (
      <ol className="list-decimal space-y-1 pl-5">
        {lines.map((line, idx) => <li key={idx}>{line.replace(/^\d+[\.\)]\s+/, "")}</li>)}
      </ol>
    );
  }

  return <p className="whitespace-pre-wrap leading-7">{content}</p>;
}

export default function SummarySection({ section, style = "academic", expanded = true, onToggle }) {
  const colorClass = STYLE_MAP[style] || STYLE_MAP.academic;
  const cleanedContent = removeDuplicateSectionTitle(section.title, section.content);

  return (
    <article className={`rounded-xl border border-zinc-200 border-l-4 p-4 ${colorClass}`}>
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-zinc-900">{section.title}</h4>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-zinc-300 bg-white text-zinc-600 hover:bg-zinc-50"
          aria-label={expanded ? "Thu gọn" : "Mở rộng"}
        >
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
      </div>
      {expanded ? <div className="text-sm text-zinc-700">{renderContent(cleanedContent)}</div> : null}
    </article>
  );
}
