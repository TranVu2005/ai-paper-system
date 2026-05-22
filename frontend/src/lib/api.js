function normalizeApiBase(raw) {
  const fallback = "http://localhost:8000/api/v1";
  if (!raw || typeof raw !== "string") return fallback;

  let base = raw.trim().replace(/\/+$/, "");

  // Common misconfig: pointing to an endpoint instead of API root.
  base = base.replace(/\/auth\/google\/config$/i, "");

  // Ensure expected backend prefix is present.
  if (!/\/api\/v1$/i.test(base)) {
    if (/\/api\/v1\//i.test(base)) {
      base = base.replace(/(\/api\/v1).*/i, "$1");
    } else {
      base = `${base}/api/v1`;
    }
  }
  return base;
}

const API_BASE = normalizeApiBase(import.meta.env.VITE_API_BASE_URL);

function cleanParams(params = {}) {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== ""),
  );
}

function getToken() {
  return localStorage.getItem("access_token");
}

function saveSession(data) {
  localStorage.setItem("access_token", data.access_token);
  localStorage.setItem("refresh_token", data.refresh_token);
  localStorage.setItem("current_user", JSON.stringify(data.user));
}

function saveTokenPair(accessToken, refreshToken) {
  localStorage.setItem("access_token", accessToken);
  localStorage.setItem("refresh_token", refreshToken);
}

function clearSession() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("current_user");
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = {
    ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const error = await response.json().catch(() => ({}));
      const detail = error?.detail;
      if (typeof detail === "string" && detail) throw new Error(detail);
      if (Array.isArray(detail) && detail.length && detail[0]?.msg) throw new Error(detail[0].msg);
      throw new Error(error?.message || "Request failed");
    }
    const raw = await response.text().catch(() => "");
    throw new Error(raw || "Request failed");
  }

  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  getToken,
  getCurrentUser: () => {
    const raw = localStorage.getItem("current_user");
    return raw ? JSON.parse(raw) : null;
  },
  clearSession,
  saveTokenPair,
  login: async (email, password) => {
    const data = await request("/auth/login/email", {
      method: "POST",
      body: JSON.stringify({ email, password, device_id: "web" }),
    });
    saveSession(data);
    return data;
  },
  loginWithGoogle: async (idToken) => {
    const data = await request("/auth/login/google", {
      method: "POST",
      body: JSON.stringify({ id_token: idToken, device_id: "web-google" }),
    });
    saveSession(data);
    return data;
  },
  startGoogleLogin: () => {
    const frontendOrigin = encodeURIComponent(window.location.origin);
    window.location.href = `${API_BASE}/auth/google/login?frontend_origin=${frontendOrigin}`;
  },
  register: (payload) =>
    request("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  sendForgotPasswordCode: (email) =>
    request("/auth/forgot-password/send-code", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  verifyForgotPasswordCode: (email, code) =>
    request("/auth/forgot-password/verify-code", {
      method: "POST",
      body: JSON.stringify({ email, code }),
    }),
  resetPasswordWithCode: (email, code, newPassword) =>
    request("/auth/forgot-password/reset-password", {
      method: "POST",
      body: JSON.stringify({ email, code, new_password: newPassword }),
    }),
  me: () => request("/users/me"),
  updateProfile: (payload) =>
    request("/users/me", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  changePassword: (payload) =>
    request("/users/me/change-password", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  userDashboard: () => request("/users/me/dashboard"),
  dashboard: () => request("/cms/dashboard/overview"),
  listWorkspaces: (params = {}) => {
    const search = new URLSearchParams(params).toString();
    return request(`/workspaces/${search ? `?${search}` : ""}`);
  },
  createWorkspace: (title = "Untitled notebook") =>
    request("/workspaces/", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  getWorkspace: (workspaceId) => request(`/workspaces/${workspaceId}`),
  updateWorkspace: (workspaceId, payload) =>
    request(`/workspaces/${workspaceId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteWorkspace: (workspaceId) =>
    request(`/workspaces/${workspaceId}`, {
      method: "DELETE",
    }),
  listDocuments: (params = {}) => {
    const search = new URLSearchParams(params).toString();
    return request(`/documents/${search ? `?${search}` : ""}`);
  },
  getDocument: (documentId) => request(`/documents/${documentId}`),
  uploadDocument: (file, workspaceId = null) => {
    const formData = new FormData();
    formData.append("file", file);
    if (workspaceId) formData.append("workspace_id", workspaceId);
    return request("/documents/upload", {
      method: "POST",
      body: formData,
    });
  },
  updateDocumentMetadata: (documentId, payload) =>
    request(`/documents/${documentId}/metadata`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  getDocumentMetadata: (documentId) => request(`/documents/${documentId}/metadata`),
  deleteDocument: (documentId) =>
    request(`/documents/${documentId}`, {
      method: "DELETE",
    }),
  getDocumentStatus: (documentId) => request(`/documents/${documentId}/status`),
  updateDocumentStatus: (documentId, status) =>
    request(`/documents/${documentId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  getDocumentTimeline: (documentId) => request(`/documents/${documentId}/timeline`),
  downloadDocumentUrl: (documentId) => `${API_BASE}/documents/${documentId}/file`,
  downloadDocument: async (documentId, filename = "document") => {
    const token = getToken();
    const response = await fetch(`${API_BASE}/documents/${documentId}/file`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new Error("Không tải được file");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  },
  getSummary: (documentId, summaryStyle = null) => {
    const style = summaryStyle ? String(summaryStyle).toLowerCase() : "";
    const query = style ? `?summary_style=${encodeURIComponent(style)}` : "";
    return request(`/cms/documents/${documentId}/summary${query}`);
  },
  requestProcessing: (documentId) =>
    request(`/cms/documents/${documentId}/process/request`, {
      method: "POST",
    }),
  requestSummaryAcademic: (documentId, level = "medium") =>
    request(`/cms/documents/${documentId}/summary/request/academic`, {
      method: "POST",
      body: JSON.stringify({ level }),
    }),
  requestSummarySemantic: (documentId, level = "medium") =>
    request(`/cms/documents/${documentId}/summary/request/semantic`, {
      method: "POST",
      body: JSON.stringify({ level }),
    }),
  requestSummaryExecutive: (documentId, level = "medium") =>
    request(`/cms/documents/${documentId}/summary/request/executive`, {
      method: "POST",
      body: JSON.stringify({ level }),
    }),
  requestSummaryByStyle: (documentId, summaryStyle = "academic", level = "medium") => {
    const style = String(summaryStyle || "academic").toLowerCase();
    if (style === "semantic") return api.requestSummarySemantic(documentId, level);
    if (style === "executive") return api.requestSummaryExecutive(documentId, level);
    return api.requestSummaryAcademic(documentId, level);
  },
  requestQuestion: (documentId, question) =>
    request(`/cms/documents/${documentId}/qa/request`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  getGraph: (documentId) => request(`/cms/documents/${documentId}/graph`),
  getRecommendations: (documentId) =>
    request(`/cms/documents/${documentId}/recommendations`),
  getQaHistory: (documentId) => request(`/documents/${documentId}/qa`),
  documentSearch: (documentId, query, limit = 10) =>
    request(`/cms/documents/${documentId}/search`, {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),
  requestDocumentSearch: (documentId, query, limit = 10) =>
    request(`/cms/documents/${documentId}/search/request`, {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),
  getJobStatus: (jobId) => request(`/cms/jobs/${jobId}`),
  search: (query, limit = 10) =>
    request("/cms/search", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),
  requestSearch: (query, limit = 10) =>
    request("/cms/search/request", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),
  internalDemoSummary: (payload) =>
    request("/internal/ai/demo/summary", {
      method: "POST",
      headers: { "x-internal-token": import.meta.env.VITE_INTERNAL_API_TOKEN || "demo-internal-token" },
      body: JSON.stringify(payload),
    }),
  internalDemoRagQa: (payload) =>
    request("/internal/ai/demo/rag-qa", {
      method: "POST",
      headers: { "x-internal-token": import.meta.env.VITE_INTERNAL_API_TOKEN || "demo-internal-token" },
      body: JSON.stringify(payload),
    }),
  internalDemoRagReset: (sessionId) =>
    request("/internal/ai/demo/rag-qa/session/reset", {
      method: "POST",
      headers: { "x-internal-token": import.meta.env.VITE_INTERNAL_API_TOKEN || "demo-internal-token" },
      body: JSON.stringify({ session_id: sessionId }),
    }),
  analytics: (groupBy = "year") =>
    request(`/cms/analytics/overview?group_by=${groupBy}`),
  adminOverview: () => request("/admin/overview"),
  adminUsers: (params = {}) => {
    const search = new URLSearchParams(cleanParams(params)).toString();
    return request(`/admin/users${search ? `?${search}` : ""}`);
  },
  adminDocuments: (params = {}) => {
    const search = new URLSearchParams(cleanParams(params)).toString();
    return request(`/admin/documents${search ? `?${search}` : ""}`);
  },
  updateAdminUser: (userId, payload) =>
    request(`/admin/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
};
