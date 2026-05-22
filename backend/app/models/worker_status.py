from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WorkerStatus(Base):
    __tablename__ = "worker_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    worker_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    current_job_id: Mapped[int | None] = mapped_column(BigInteger)
    last_heartbeat: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

