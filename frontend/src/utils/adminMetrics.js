export function calculatePercentage(part, total) {
  if (!total || total <= 0) return 0;
  return Math.max(0, Math.min(100, (part / total) * 100));
}

export function calculateSystemHealthScore(kpis) {
  const docFailureRate = calculatePercentage(kpis.failed || 0, kpis.total_documents || 0);
  const jobFailureRate = calculatePercentage(kpis.jobs_failed || 0, kpis.total_jobs || 0);
  return Math.round(Math.max(0, 100 - (docFailureRate * 0.6 + jobFailureRate * 0.4)));
}

export function calculateDataReadinessScore(kpis) {
  return Math.round(calculatePercentage(kpis.processed || 0, kpis.total_documents || 0));
}

export function calculateAIUsageScore(kpis) {
  const usage = (kpis.total_qa || 0) + (kpis.total_summaries || 0);
  const baseline = Math.max(1, (kpis.total_documents || 0) * 3);
  return Math.round(Math.min(100, calculatePercentage(usage, baseline)));
}

export function calculateUserActivityScore(kpis) {
  return Math.round(calculatePercentage(kpis.active_users || 0, kpis.total_users || 0));
}

export function buildExecutiveInsights(kpis, analytical = {}) {
  const failedRate = calculatePercentage((kpis.failed || 0) + (kpis.jobs_failed || 0), (kpis.total_documents || 0) + (kpis.total_jobs || 0));
  const processedRate = calculatePercentage(kpis.processed || 0, kpis.total_documents || 0);
  const aiUsage = (kpis.total_qa || 0) + (kpis.total_summaries || 0);
  const activeRate = calculatePercentage(kpis.active_users || 0, kpis.total_users || 0);

  const mostActiveYear = (analytical.byYear || []).slice().sort((a, b) => b.count - a.count)[0]?.value ?? "N/A";
  const mostCommonTopic = (analytical.topTopics || [])[0]?.value ?? "N/A";
  const topContributor = (analytical.topAuthors || [])[0]?.name ?? (analytical.topAuthors || [])[0]?.value ?? "N/A";

  const messages = [];
  if (failedRate > 15) messages.push("Failed rate cao, cần kiểm tra ingestion pipeline và retry policy.");
  if (processedRate >= 75) messages.push("Processed rate cao, dữ liệu sẵn sàng tốt cho AI.");
  if (aiUsage < 20) messages.push("AI usage thấp, nên đẩy mạnh flow QA/Summary trong UI.");
  if (activeRate < 50) messages.push("Tỷ lệ active users thấp, cần cải thiện engagement.");
  if (messages.length === 0) messages.push("Hệ thống đang ở trạng thái ổn định và cân bằng.");

  return {
    mostActiveYear,
    mostCommonTopic,
    topContributor,
    messages,
  };
}

export function buildRiskRecommendations(kpis) {
  const docFailedRate = calculatePercentage(kpis.failed || 0, kpis.total_documents || 0);
  const jobFailedRate = calculatePercentage(kpis.jobs_failed || 0, kpis.total_jobs || 0);
  const activeRate = calculatePercentage(kpis.active_users || 0, kpis.total_users || 0);
  const aiUsage = (kpis.total_qa || 0) + (kpis.total_summaries || 0);

  const riskLevel = (value, high, medium) => (value >= high ? "High" : value >= medium ? "Medium" : "Low");
  return [
    {
      area: "Ingestion",
      risk_level: riskLevel(docFailedRate, 15, 7),
      issue: `Document failed rate: ${docFailedRate.toFixed(1)}%`,
      recommendation: "Kiểm tra parser lỗi, thêm retry theo loại file và theo dõi queue backlog.",
    },
    {
      area: "AI Jobs",
      risk_level: riskLevel(jobFailedRate, 12, 5),
      issue: `Job failed rate: ${jobFailedRate.toFixed(1)}%`,
      recommendation: "Rà soát timeout model, log error_message và chuẩn hóa retry_count.",
    },
    {
      area: "Engagement",
      risk_level: activeRate < 50 ? "High" : activeRate < 70 ? "Medium" : "Low",
      issue: `Active users ratio: ${activeRate.toFixed(1)}%`,
      recommendation: "Tăng notification và tối ưu workflow để kéo người dùng quay lại.",
    },
    {
      area: "AI Feature Adoption",
      risk_level: aiUsage < 20 ? "Medium" : "Low",
      issue: `Total QA + Summaries: ${aiUsage}`,
      recommendation: "Đặt CTA rõ ràng cho QA/Summary trên trang document detail.",
    },
  ];
}
