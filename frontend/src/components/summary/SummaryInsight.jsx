function formatNumber(value) {
  return Number(value || 0).toLocaleString("vi-VN");
}

export default function SummaryInsight({ style = "academic", sectionCount = 0, charCount = 0 }) {
  const styleLabel = style.charAt(0).toUpperCase() + style.slice(1);
  return (
    <div className="rounded-xl border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-700">
      <span className="font-medium text-zinc-900">{styleLabel} Summary</span>
      <span className="mx-2 text-zinc-400">•</span>
      <span>{sectionCount} mục chính</span>
      <span className="mx-2 text-zinc-400">•</span>
      <span>{formatNumber(charCount)} ký tự</span>
    </div>
  );
}

