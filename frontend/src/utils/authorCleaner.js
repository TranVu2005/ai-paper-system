const STOP_WORDS = new Set([
  "lấy", "chủ", "trong", "đó", "thường", "gặp", "nhất", "là", "có", "thể",
  "the", "and", "of",
]);

function hasStopWords(name) {
  const words = String(name || "")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  return words.some((w) => STOP_WORDS.has(w));
}

function hasReasonableCapitalization(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length < 2) return false;
  const titleCaseCount = words.filter((w) => /^[A-ZÀ-Ỹ][a-zà-ỹ'’.-]*$/u.test(w)).length;
  return titleCaseCount >= Math.ceil(words.length / 2);
}

function looksLikeSentence(name) {
  const s = String(name || "");
  return /[,:;.!?]/.test(s) || /\b(và|là|của|những|được|trong|the|and|of)\b/iu.test(s);
}

export function isValidAuthorName(name) {
  const raw = String(name || "").trim();
  if (!raw) return false;
  if (raw.length > 60) return false;
  const words = raw.split(/\s+/).filter(Boolean);
  if (words.length < 2) return false;
  if (hasStopWords(raw)) return false;
  if (looksLikeSentence(raw)) return false;
  if (!hasReasonableCapitalization(raw)) return false;
  if (/[0-9]/.test(raw)) return false;
  return true;
}

export function cleanTopAuthors(authors) {
  const mapped = (authors || [])
    .map((a) => ({
      name: String(a?.name ?? a?.value ?? "").trim(),
      count: Number(a?.count || 0),
    }))
    .filter((a) => a.count > 0 && isValidAuthorName(a.name));

  const dedup = new Map();
  for (const item of mapped) {
    const key = item.name.toLowerCase();
    const old = dedup.get(key);
    if (!old || item.count > old.count) dedup.set(key, item);
  }

  return Array.from(dedup.values())
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
}

