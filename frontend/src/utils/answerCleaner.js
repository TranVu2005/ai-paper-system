export function cleanAnswerText(text) {
  let value = String(text || "");

  value = value.replace(/\n?\s*(Nguồn:|Source:)\s*[\s\S]*$/i, "");
  value = value.replace(/\n?\s*-\s*file:\s*[\s\S]*$/i, "");

  value = value.replace(/file:\s*.*?(,|$)/gi, " ");
  value = value.replace(/page:\s*None/gi, " ");
  value = value.replace(/page:\s*\d+/gi, " ");
  value = value.replace(/chunk:\s*\d+/gi, " ");

  value = value
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();

  return value;
}

