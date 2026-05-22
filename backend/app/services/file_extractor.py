from PyPDF2 import PdfReader
from docx import Document as DocxDocument


def extract_text_from_docx(file_path: str) -> str:
    doc = DocxDocument(file_path)
    text = ""

    for para in doc.paragraphs:
        text += para.text + "\n"

    return text


def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    reader = PdfReader(file_path)

    for page in reader.pages:
        text += page.extract_text() or ""

    return text


def extract_text_from_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()
