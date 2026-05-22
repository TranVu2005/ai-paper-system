export function shouldUseDonutChart(data) {
  const rows = (data || []).filter((x) => Number(x?.value || 0) > 0);
  if (rows.length <= 2) return true;
  const total = rows.reduce((sum, item) => sum + Number(item.value || 0), 0);
  if (!total) return true;
  const maxVal = Math.max(...rows.map((r) => Number(r.value || 0)));
  const maxPct = (maxVal / total) * 100;
  return maxPct > 85;
}

