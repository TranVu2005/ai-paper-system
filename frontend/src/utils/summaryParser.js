const TITLE_HINTS = [
  "kết luận chính",
  "bằng chứng",
  "ý nghĩa",
  "hạn chế",
  "khuyến nghị",
];

const VI_TITLE_NORMALIZE_MAP = {
  "dat van de": "Đặt vấn đề",
  "van de nghien cuu": "Vấn đề nghiên cứu",
  "phuong phap de xuat": "Phương pháp đề xuất",
  "phuong phap": "Phương pháp",
  "cach trien khai": "Cách triển khai",
  "ket qua": "Kết quả",
  "ket luan": "Kết luận",
  "ket luab": "Kết luận",
};

export function detectSectionTitle(content, index) {
  const line = String(content || "").split("\n")[0]?.trim() || "";
  if (!line) return `Mục ${index + 1}`;
  const lowered = line.toLowerCase();

  const normalized = lowered
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (VI_TITLE_NORMALIZE_MAP[normalized]) return VI_TITLE_NORMALIZE_MAP[normalized];

  const hint = TITLE_HINTS.find((item) => lowered.includes(item));
  if (hint) return hint.charAt(0).toUpperCase() + hint.slice(1);

  const cleaned = line
    .replace(/^(\d+[\.\)]\s*|[-*•]\s*)/, "")
    .trim()
    .slice(0, 72);
  return cleaned || `Mục ${index + 1}`;
}

function normalizeText(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .trim();
}

export function parseSummarySections(text) {
  const normalized = normalizeText(text);
  if (!normalized) return [];

  const lines = normalized.split("\n");
  const numberedCount = lines.filter((line) => /^\s*\d+[\.\)]\s+/.test(line)).length;
  const bulletCount = lines.filter((line) => /^\s*[-*•]\s+/.test(line)).length;

  let rawSections = [];

  if (numberedCount >= 2) {
    const blocks = normalized.split(/\n(?=\s*\d+[\.\)]\s+)/).map((s) => s.trim()).filter(Boolean);
    rawSections = blocks.map((block) => block.replace(/^\s*\d+[\.\)]\s+/, "").trim()).filter(Boolean);
  } else if (bulletCount >= 2) {
    rawSections = lines
      .filter((line) => /^\s*[-*•]\s+/.test(line))
      .map((line) => line.replace(/^\s*[-*•]\s+/, "").trim())
      .filter(Boolean);
  } else {
    rawSections = normalized
      .split(/\n\s*\n+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  return rawSections.map((content, index) => ({
    id: `${index + 1}`,
    title: detectSectionTitle(content, index),
    content,
    index: index + 1,
  }));
}
