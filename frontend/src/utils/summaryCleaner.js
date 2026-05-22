function normalize(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function removeDuplicateSectionTitle(title, content) {
  const text = String(content || "").replace(/\r\n/g, "\n").trim();
  if (!text) return "";

  const lines = text.split("\n");
  const firstLine = String(lines[0] || "").trim();

  if (normalize(firstLine) === normalize(title)) {
    return lines.slice(1).join("\n").trim();
  }

  return text;
}

