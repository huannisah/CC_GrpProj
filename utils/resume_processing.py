"""
resume_processing.py

Extracts plain text from uploaded resume files (PDF, TXT, DOCX).
Used by web_routes when the user uploads a file via the UI form.
"""

import PyPDF2
from docx import Document


def process_resume(file_path: str) -> str:
    """Extract text from PDF, TXT, or DOCX files."""
    ext = file_path.rsplit('.', 1)[-1].lower()

    if ext == 'pdf':
        return _extract_pdf(file_path)
    elif ext == 'txt':
        return _extract_txt(file_path)
    elif ext == 'docx':
        return _extract_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: .{ext}. Please upload a PDF, DOCX, or TXT file.")


def _extract_pdf(file_path: str) -> str:
    text = ""
    try:
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted
    except Exception as e:
        raise ValueError(f"Could not read PDF file: {e}")

    if not text.strip():
        raise ValueError("The PDF appears to contain no readable text. It may be scanned or image-based.")
    return text


def _extract_txt(file_path: str) -> str:
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def _extract_docx(file_path: str) -> str:
    try:
        doc = Document(file_path)
        return '\n'.join([para.text for para in doc.paragraphs])
    except Exception as e:
        raise ValueError(f"Could not read DOCX file: {e}")
