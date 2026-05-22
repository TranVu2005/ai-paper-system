from sqlalchemy.orm import Session
from pathlib import Path
from fastapi import UploadFile

from app.models.document import Document
from app.models.document_job import DocumentJob
from app.services.event_publisher import publish_document_uploaded


async def handle_upload_document(
    db: Session,
    file: UploadFile,
    user_id: int,
    workspace_id: int | None = None,
):
    backend_dir = Path(__file__).resolve().parents[3]
    upload_dir = backend_dir / "uploaded_files"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / file.filename

    # save file
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    # create document
    document = Document(
        filename=file.filename,
        file_type=file.content_type,
        user_id=user_id,
        workspace_id=workspace_id,
        status="uploaded",
    )

    db.add(document)
    db.commit()
    db.refresh(document)

    # create processing job (ingestion worker/background will handle it)
    job = DocumentJob(
        document_id=document.id,
        job_type="process_document",
        requested_by_user_id=user_id,
        payload={"filename": document.filename, "file_type": document.file_type},
    )
    db.add(job)
    db.commit()

    # publish event
    publish_document_uploaded(
        document_id=document.id,
        user_id=user_id
    )

    return document
