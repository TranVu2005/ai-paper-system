import { mapApiDocument } from "@/lib/documentMapper";

export function mapApiWorkspace(workspace) {
  const documents = (workspace.documents || []).map(mapApiDocument);
  const firstDocument = documents[0];

  return {
    id: workspace.id,
    title: workspace.title || firstDocument?.title || "Untitled notebook",
    documentCount: workspace.document_count ?? documents.length,
    documents,
    updated: workspace.updated_at
      ? new Date(workspace.updated_at).toLocaleString("vi-VN")
      : workspace.created_at
        ? new Date(workspace.created_at).toLocaleString("vi-VN")
        : "",
    createdAt: workspace.created_at,
    raw: workspace,
  };
}

export function mapApiWorkspaces(response, fallback = []) {
  const items = Array.isArray(response) ? response : response?.items;
  return items?.length ? items.map(mapApiWorkspace) : fallback;
}
