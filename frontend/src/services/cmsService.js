import { api } from "@/lib/api";

export async function renameSession(sessionId, title) {
  return api.updateWorkspace(sessionId, { title });
}

export async function deleteSession(sessionId) {
  return api.deleteWorkspace(sessionId);
}

export async function deleteDocument(documentId) {
  return api.deleteDocument(documentId);
}

