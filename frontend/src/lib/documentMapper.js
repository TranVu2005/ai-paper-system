export function mapApiDocument(document) {
  const metadata = document.metadata || {};
  const filename = document.filename || "untitled";
  const extension = filename.split(".").pop()?.toUpperCase() || document.file_type || "FILE";

  return {
    id: document.id,
    title: metadata.title || filename,
    filename,
    type: extension,
    status: document.status || "uploaded",
    year: metadata.publication_year || "N/A",
    authors: metadata.authors?.length ? metadata.authors : ["Chưa có tác giả"],
    topics: metadata.topics?.length ? metadata.topics : metadata.keywords || [],
    methods: metadata.methods || [],
    pages: metadata.pages || "-",
    updated: document.created_at ? new Date(document.created_at).toLocaleString("vi-VN") : "",
    abstract: metadata.abstract || "Chưa có mô tả.",
    raw: document,
  };
}

export function mapApiDocuments(response, fallback = []) {
  const items = Array.isArray(response) ? response : response?.items;
  return items?.length ? items.map(mapApiDocument) : fallback;
}
