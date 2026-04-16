"""
web_routes.py

Server-side rendered web UI routes.
These serve HTML pages using Jinja2 templates and use session state
to pass data between the upload step and the tool steps.
"""

import os
import re
import shutil
import tempfile
from typing import Optional

import mistune
import requests as http_requests
from bs4 import BeautifulSoup
from docx import Document
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

from config import Config
from utils.resume_processing import process_resume
from utils.openai_service import (
    get_ats_score, fine_tune_resume, generate_cover_letter,
    analyze_job_posting, extract_market_insights
)
from utils.analytics_service import (
    record_insights, get_top_technical_skills, get_top_soft_skills,
    get_top_keywords, get_industry_breakdown,
    get_seniority_breakdown, get_total_submissions
)

router = APIRouter()

# Jinja2 template directory
templates = Jinja2Templates(directory="templates")


# ── Flash helpers (replicate Flask's flash() using Starlette sessions) ────────

def flash(request: Request, message: str, category: str = "info"):
    """Store a flash message in the session."""
    if "_flash" not in request.session:
        request.session["_flash"] = []
    request.session["_flash"].append({"message": message, "category": category})


def get_flashed(request: Request) -> list:
    """Retrieve and clear flash messages from the session."""
    return request.session.pop("_flash", [])


# ── Helpers ───────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}


def allowed_file(filename: str) -> bool:
    """Check whether the filename has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def secure_filename(filename: str) -> str:
    """
    Sanitise a filename — replaces werkzeug.utils.secure_filename
    so we don't need the full Werkzeug dependency.
    Keeps only alphanumeric characters, dots, hyphens, and underscores.
    """
    # Remove any path separators
    filename = filename.replace('/', '_').replace('\\', '_')
    # Keep only safe characters
    filename = re.sub(r'[^\w\.\-]', '_', filename)
    # Remove leading dots (hidden files)
    filename = filename.lstrip('.')
    return filename or 'unnamed_file'


def extract_jd_from_url(url: str) -> str | None:
    """Use Jina Reader to extract clean text from a job posting URL."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = http_requests.get(
            jina_url,
            timeout=20,
            headers={"Accept": "text/plain"}
        )
        if resp.status_code == 200 and resp.text.strip():
            return resp.text.strip()
        return None
    except http_requests.exceptions.Timeout:
        return None
    except Exception as e:
        print(f"URL fetch error: {e}")
        return None


def save_as_docx(text: str, file_path: str):
    """Save plain text as a .docx file."""
    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    doc.save(file_path)


def get_session_texts(request: Request) -> tuple:
    """Read resume and JD texts from temp files referenced in the session."""
    resume_text, jd_text = None, None
    resume_path = request.session.get('resume_path')
    jd_path = request.session.get('jd_path')
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, 'r', encoding='utf-8') as f:
            resume_text = f.read()
    if jd_path and os.path.exists(jd_path):
        with open(jd_path, 'r', encoding='utf-8') as f:
            jd_text = f.read()
    return resume_text, jd_text

def get_file_names(request: Request) -> tuple:
    """Extract just the filenames from session paths for display in sidebar."""
    resume_name, jd_name = None, None
    resume_path = request.session.get('resume_path')
    jd_path = request.session.get('jd_path')
    if resume_path:
        resume_name = request.session.get('resume_name', 'Resume')
    if jd_path:
        jd_name = request.session.get('jd_name', 'Job Description')
    return resume_name, jd_name

async def save_upload(upload: UploadFile) -> str:
    """Save an uploaded file to the uploads/ directory."""
    filename = secure_filename(upload.filename)
    path = os.path.join(Config.UPLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return path


def write_temp_txt(text: str) -> str:
    """Write text to a temporary file and return the path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w', encoding='utf-8')
    tmp.write(text)
    tmp.close()
    return tmp.name


# ── Page Routes ───────────────────────────────────────────────────────────────

@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/about")
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html")


@router.get("/upload")
async def upload_page(request: Request):
    messages = get_flashed(request)
    # Check if both files are already in session
    has_files = bool(
        request.session.get('resume_path') and 
        request.session.get('jd_path')
    )
    return templates.TemplateResponse(request, "upload.html", {
        "messages": messages,
        "has_files": has_files
    })


@router.post("/upload")
async def upload_file(
    request: Request,
    resume: UploadFile = File(...),
    job_description: UploadFile = File(None),
    job_text: Optional[str] = Form(None),
    job_url: Optional[str] = Form(None),
):
    # ── Resume ────────────────────────────────────────────────────────────
    if not resume or not resume.filename or not allowed_file(resume.filename):
        flash(request, "Please upload a valid resume file (pdf, docx, or txt).", "error")
        return RedirectResponse(url="/upload", status_code=303)

    resume_path = await save_upload(resume)

    if os.path.getsize(resume_path) == 0:
        flash(request, "Uploaded resume appears to be empty. Please try again.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        resume_text = process_resume(resume_path)
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse(url="/upload", status_code=303)

    request.session['resume_path'] = write_temp_txt(resume_text)
    request.session['resume_name'] = resume.filename

    # ── Job Description: file → pasted text → URL ─────────────────────────
    jd_text = None

    if job_description and job_description.filename and allowed_file(job_description.filename):
        jd_path = await save_upload(job_description)
        try:
            jd_text = process_resume(jd_path)
        except ValueError as e:
            flash(request, str(e), "error")
            return RedirectResponse(url="/upload", status_code=303)

    elif job_text and job_text.strip():
        jd_text = job_text.strip()

    elif job_url and job_url.strip():
        jd_text = extract_jd_from_url(job_url.strip())
        if not jd_text:
            flash(request, "Could not extract job description from the URL. Please paste the text directly instead.", "error")
            return RedirectResponse(url="/upload", status_code=303)
    else:
        flash(request, "Please provide a job description — upload a file, paste the text, or enter a URL.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    request.session['jd_path'] = write_temp_txt(jd_text)
        # For file upload:
    request.session['jd_name'] = job_description.filename if job_description and job_description.filename else 'Job Description'
    # For pasted text or URL, it will fall back to 'Job Description'

    # ── Dual-utility: harvest market insights (non-fatal if it fails) ─────
    try:
        insights = extract_market_insights(jd_text)
        record_insights(insights)
    except Exception as e:
        print(f"Analytics extraction failed (non-fatal): {e}")

    return RedirectResponse(url="/tools", status_code=303)


@router.get("/ats_scores")
async def ats_scores(request: Request):
    resume_text, jd_text = get_session_texts(request)
    if not (resume_text and jd_text):
        flash(request, "Please upload both a resume and a job description first.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        score, feedback = get_ats_score(resume_text, jd_text)
    except Exception as e:
        flash(request, f"Error generating ATS score: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    feedback_html = mistune.create_markdown()(feedback)
    resume_name, jd_name = get_file_names(request)
    return templates.TemplateResponse(request, "ats_score.html", {"score": score, "feedback": feedback_html, "resume_name": resume_name, "jd_name": jd_name})


@router.get("/fine_tune")
async def fine_tune(request: Request):
    resume_text, jd_text = get_session_texts(request)
    if not (resume_text and jd_text):
        flash(request, "Please upload both a resume and a job description before fine-tuning.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        optimized = fine_tune_resume(resume_text, jd_text)
    except Exception as e:
        flash(request, f"Error optimising resume: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    optimized_html = mistune.create_markdown()(optimized)
    resume_name, jd_name = get_file_names(request)

    # Save the optimised resume as a .docx for download
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    tmp.close()
    save_as_docx(optimized, tmp.name)

    # Store the download path in session (not as a query param — avoids path traversal)
    request.session['download_resume_path'] = tmp.name

    return templates.TemplateResponse(request, "optimization_report.html", {
        "optimized_resume": optimized_html,
        "report": "Fine-tuning complete. Resume is now ATS-friendly.",
        "download_path": tmp.name,
        "resume_name": resume_name,
        "jd_name": jd_name
    })


@router.get("/generate_cover_letter")
async def generate_cover_letter_route(request: Request):
    resume_text, jd_text = get_session_texts(request)
    if not (resume_text and jd_text):
        flash(request, "Please upload both a resume and a job description before generating a cover letter.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        cover_letter_md = generate_cover_letter(resume_text, jd_text)
    except Exception as e:
        flash(request, f"Error generating cover letter: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    cover_letter_html = mistune.create_markdown()(cover_letter_md)
    resume_name, jd_name = get_file_names(request)

    # Save the cover letter as a .docx for download
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    tmp.close()
    save_as_docx(cover_letter_md, tmp.name)

    # Store the download path in session
    request.session['download_cover_letter_path'] = tmp.name

    return templates.TemplateResponse(request, "cover_letter.html", {
        "cover_letter": cover_letter_html,
        "download_path": tmp.name,
        "resume_name": resume_name,
        "jd_name": jd_name
    })


@router.get("/analyze_job_posting")
async def analyze_job_posting_route(request: Request):
    _, jd_text = get_session_texts(request)
    if not jd_text:
        flash(request, "Please upload a job description before analyzing it.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        analysis = analyze_job_posting(jd_text)
    except Exception as e:
        flash(request, f"Error analysing job posting: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    analysis_html = mistune.create_markdown()(analysis)
    resume_name, jd_name = get_file_names(request)
    return templates.TemplateResponse(request, "job_analysis.html", {"analysis": analysis_html, "resume_name": resume_name, "jd_name": jd_name})


@router.get("/dashboard")
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "total": get_total_submissions(),
        "tech_skills": get_top_technical_skills(),
        "soft_skills": get_top_soft_skills(),
        "keywords": get_top_keywords(),
        "industries": get_industry_breakdown(),
        "seniority": get_seniority_breakdown(),
    })


@router.get("/download_report")
async def download_report(path: str):
    """Download the optimised resume as a .docx file."""
    # Security: only serve files from the temp directory that end with .docx
    if not path.endswith('.docx') or not os.path.exists(path):
        return RedirectResponse(url="/upload", status_code=303)
    return FileResponse(path, media_type="application/octet-stream", filename="optimized_resume.docx")


@router.get("/download_cover_letter")
async def download_cover_letter(path: str):
    """Download the generated cover letter as a .docx file."""
    if not path.endswith('.docx') or not os.path.exists(path):
        return RedirectResponse(url="/upload", status_code=303)
    return FileResponse(path, media_type="application/octet-stream", filename="cover_letter.docx")

@router.get("/tools")
async def tools_page(request: Request):
    resume_path = request.session.get('resume_path')
    jd_path = request.session.get('jd_path')
    # If no files uploaded yet, send back to upload
    if not (resume_path and jd_path):
        flash(request, "Please upload your resume and job description first.", "error")
        return RedirectResponse(url="/upload", status_code=303)
    resume_name, jd_name = get_file_names(request)
    return templates.TemplateResponse(request, "tools.html", {
        "resume_name": resume_name,
        "jd_name": jd_name,
    })

@router.get("/clear")
async def clear_session(request: Request):
    request.session.clear()
    flash(request, "Session cleared.", "success")
    return RedirectResponse(url="/upload", status_code=303)
