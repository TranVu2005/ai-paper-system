export const currentUser = {
  id: 1,
  fullName: "Nguyễn Văn A",
  email: "user@example.com",
  role: "user",
  active: true,
  organization: "Khoa Công nghệ thông tin",
};

export const documents = [
  {
    id: 1,
    title: "Mô hình RAG cho tóm tắt văn bản tiếng Việt",
    filename: "rag-vietnamese.pdf",
    type: "PDF",
    status: "processed",
    year: 2025,
    authors: ["Nguyễn Văn A", "Trần Thị B"],
    topics: ["RAG", "NLP", "Vietnamese"],
    methods: ["Chunking", "Embedding", "Hybrid Retrieval"],
    pages: 26,
    updated: "10 phút trước",
    abstract:
      "Nghiên cứu trình bày pipeline RAG cho tài liệu tiếng Việt, từ tiền xử lý, chia đoạn, tạo embedding đến sinh câu trả lời có căn cứ.",
  },
  {
    id: 2,
    title: "Khai phá dữ liệu trong phân tích y sinh học",
    filename: "biomedical-data-mining.docx",
    type: "DOCX",
    status: "processing",
    year: 2024,
    authors: ["Lê Văn C"],
    topics: ["Data Mining", "Biomedical"],
    methods: ["Classification", "Clustering"],
    pages: 18,
    updated: "1 giờ trước",
    abstract:
      "Tài liệu tổng hợp các kỹ thuật khai phá dữ liệu ứng dụng trong phân tích y sinh và hỗ trợ ra quyết định.",
  },
  {
    id: 3,
    title: "Ứng dụng vector database trong học thuật",
    filename: "vector-db-academic.txt",
    type: "TXT",
    status: "processed",
    year: 2023,
    authors: ["Phạm Thị D", "Hoàng Văn E"],
    topics: ["Vector Database", "Semantic Search"],
    methods: ["ANN Search", "Metadata Filtering"],
    pages: 31,
    updated: "Hôm qua",
    abstract:
      "Bài viết mô tả cách lưu trữ embedding trong vector database để tăng tốc truy xuất tài liệu khoa học liên quan.",
  },
  {
    id: 4,
    title: "Phân cụm chủ đề nghiên cứu bằng embedding",
    filename: "topic-clustering.pdf",
    type: "PDF",
    status: "uploaded",
    year: 2022,
    authors: ["Trần Thị B"],
    topics: ["Topic Modeling", "Embedding"],
    methods: ["K-means", "Dimensionality Reduction"],
    pages: 22,
    updated: "2 ngày trước",
    abstract:
      "Nghiên cứu đề xuất nhóm tài liệu theo không gian vector nhằm hỗ trợ phân tích chủ đề và gợi ý bài báo.",
  },
];

export const summaries = {
  short:
    "Tài liệu trình bày cách xây dựng hệ thống RAG cho tiếng Việt, gồm tiền xử lý, embedding, truy xuất và sinh câu trả lời có căn cứ.",
  detailed:
    "Tài liệu mô tả đầy đủ pipeline xử lý paper tiếng Việt: tải lên file PDF/DOCX/TXT, trích xuất nội dung, chia chunk theo ngữ cảnh, tạo embedding, lưu vào vector database, kết hợp Knowledge Graph để bổ sung quan hệ giữa tác giả, chủ đề và phương pháp. Lớp RAG dùng các chunk liên quan để sinh tóm tắt và trả lời câu hỏi bám sát tài liệu gốc.",
};

export const qaHistory = [
  {
    question: "Tài liệu tập trung vào phương pháp nào?",
    answer:
      "Tài liệu tập trung vào Retrieval-Augmented Generation, kết hợp truy xuất chunk liên quan với mô hình ngôn ngữ để trả lời theo ngữ cảnh.",
    source: "Chunk 4, Chunk 7",
  },
  {
    question: "Hệ thống đảm bảo câu trả lời bám sát tài liệu bằng cách nào?",
    answer:
      "Hệ thống đưa các đoạn nội dung được truy xuất vào prompt và gắn nguồn trích dẫn cho câu trả lời.",
    source: "Chunk 8",
  },
];

export const graphNodes = [
  { id: "p1", label: "RAG cho tiếng Việt", type: "Paper", x: 48, y: 44 },
  { id: "a1", label: "Nguyễn Văn A", type: "Author", x: 18, y: 18 },
  { id: "a2", label: "Trần Thị B", type: "Author", x: 78, y: 18 },
  { id: "t1", label: "RAG", type: "Topic", x: 24, y: 76 },
  { id: "m1", label: "Hybrid Retrieval", type: "Method", x: 76, y: 74 },
];

export const graphEdges = [
  ["a1", "p1", "viết"],
  ["a2", "p1", "đồng tác giả"],
  ["p1", "t1", "thuộc chủ đề"],
  ["p1", "m1", "sử dụng"],
];

export const recommendations = [
  {
    id: 3,
    title: "Ứng dụng vector database trong học thuật",
    score: 0.92,
    reason:
      "Cùng sử dụng embedding và vector retrieval, phù hợp để mở rộng phần thiết kế truy xuất.",
  },
  {
    id: 4,
    title: "Phân cụm chủ đề nghiên cứu bằng embedding",
    score: 0.88,
    reason:
      "Liên quan đến topic modeling và không gian vector, hữu ích cho module gợi ý bài báo.",
  },
];

export const analytics = {
  byYear: [
    { value: 2025, count: 8 },
    { value: 2024, count: 13 },
    { value: 2023, count: 19 },
    { value: 2022, count: 11 },
  ],
  byTopic: [
    { value: "RAG", count: 18 },
    { value: "NLP", count: 15 },
    { value: "Semantic Search", count: 12 },
    { value: "Data Mining", count: 9 },
  ],
  byAuthor: [
    { value: "Trần Thị B", count: 7 },
    { value: "Nguyễn Văn A", count: 5 },
    { value: "Lê Văn C", count: 4 },
    { value: "Phạm Thị D", count: 3 },
  ],
};

export const users = [
  {
    id: 1,
    name: "Nguyễn Văn A",
    email: "user@example.com",
    role: "user",
    active: true,
    uploads: 24,
  },
  {
    id: 2,
    name: "Admin",
    email: "admin@example.com",
    role: "admin",
    active: true,
    uploads: 0,
  },
  {
    id: 3,
    name: "Lê Văn C",
    email: "levanc@example.com",
    role: "user",
    active: false,
    uploads: 7,
  },
];
