from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DocumentSummary(Base):
    __tablename__ = "document_summaries"
    __table_args__ = (
        UniqueConstraint("document_id", "summary_style", name="uq_document_summaries_doc_style"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    summary_style: Mapped[str] = mapped_column(String(32), nullable=False, default="academic")
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
