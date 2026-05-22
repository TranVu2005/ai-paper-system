from app.db.session import Base

# IMPORT TẤT CẢ MODEL Ở ĐÂY
from app.models.user import User
from app.models.workspace import Workspace
from app.models.document import Document
from app.models.refresh_token import RefreshToken
from app.models.document_chunk import DocumentChunk
from app.models.document_job import DocumentJob
from app.models.document_summary import DocumentSummary
from app.models.document_metadata import DocumentMetadata
from app.models.document_graph import DocumentGraph
from app.models.document_recommendation import DocumentRecommendation
from app.models.document_artifact import DocumentArtifact
from app.models.qa_history import QAHistory
from app.models.password_reset_code import PasswordResetCode
from app.models.login_event import LoginEvent
