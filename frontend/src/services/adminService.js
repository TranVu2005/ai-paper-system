import { api } from "@/lib/api";

export async function getAdminStats() {
  return api.adminOverview();
}

export async function getAdminUsers(params = {}) {
  return api.adminUsers(params);
}

export async function getAdminDocuments(params = {}) {
  return api.adminDocuments(params);
}

export async function updateUserRole(userId, role) {
  return api.updateAdminUser(userId, { role });
}

export async function lockUser(userId) {
  return api.updateAdminUser(userId, { is_active: false });
}

export async function unlockUser(userId) {
  return api.updateAdminUser(userId, { is_active: true });
}

export async function deleteUser() {
  throw new Error("Tính năng xóa người dùng chưa có API backend.");
}

export async function reprocessDocument(documentId) {
  return api.requestProcessing(documentId);
}

export async function deleteDocument(documentId) {
  return api.deleteDocument(documentId);
}
