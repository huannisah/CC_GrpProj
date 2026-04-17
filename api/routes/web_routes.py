"""
web_routes.py

Server-side rendered web UI routes.
These serve HTML pages using Jinja2 templates and use session state
to pass data between the upload step and the tool steps.
"""

import asyncio
import os
import re
import shutil
import tempfile
from typing import Optional

import mistune
import requests as http_requests
from bs4 import BeautifulSoup
from docx import Document
from fastapi import APIRouter, BackgroundTasks, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

from config import Config
from utils.resume_processing import process_resume
from utils.openai_service import (
    get_ats_score, fine_tune_resume, generate_cover_letter,
    analyze_job_posting, extract_market_insights
)
from utils.analytics_service import (
    record_insights,
    record_ats_score,
    get_ats_benchmark,
    get_top_technical_skills,
    get_top_soft_skills,
    get_top_keywords,
    get_industry_breakdown,
    get_seniority_breakdown,
    get_total_submissions,
    get_skill_trend_comparison,
    get_remote_breakdown,
    get_experience_distribution,
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
    filename = filename.replace('/', '_').replace('\\', '_')
    filename = re.sub(r'[^\w\.\-]', '_', filename)
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
    background_tasks: BackgroundTasks,  # FastAPI injects this automatically
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
    request.session['jd_name'] = (
        job_description.filename
        if job_description and job_description.filename
        else 'Job Description'
    )

    def _run_analytics(text: str):
        try:
            insights = extract_market_insights(text)
            if insights:
                record_insights(insights)
        except Exception as e:
            print(f"[background] Analytics extraction failed (non-fatal): {e}")

    background_tasks.add_task(_run_analytics, jd_text)

    # NOTE: industry + seniority are no longer stored in the session here
    # because _run_analytics runs after the redirect. The ats_scores route
    # below falls back gracefully to 'Unknown' when they are absent, which
    # was already the existing fallback behaviour.
    return RedirectResponse(url="/tools", status_code=303)


@router.get("/ats_scores")
async def ats_scores(request: Request):
    resume_text, jd_text = get_session_texts(request)
    if not (resume_text and jd_text):
        flash(request, "Please upload both a resume and a job description first.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        # asyncio.to_thread() runs the synchronous OpenAI function in a thread
        # pool worker. This keeps FastAPI's event loop free to handle other
        # requests while waiting for the OpenAI response (typically 5–15 s).
        score, feedback = await asyncio.to_thread(get_ats_score, resume_text, jd_text)
    except Exception as e:
        flash(request, f"Error generating ATS score: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    industry = request.session.get('industry', 'Unknown')
    seniority = request.session.get('seniority', 'Unknown')

    # record_ats_score is wrapped in try/except inside analytics_service —
    # it will never raise, so it cannot block or delay the page rendering.
    record_ats_score(score, industry, seniority)

    # get_ats_benchmark returns {"available": False} if there's no data yet,
    # so the template must check benchmark.available before rendering.
    benchmark = get_ats_benchmark(score, industry)

    # feedback_html = mistune.create_markdown()(feedback)
    feedback_html = mistune.create_markdown(plugins=["table"])(feedback)
    resume_name, jd_name = get_file_names(request)

    return templates.TemplateResponse(request, "ats_score.html", {
        "score":       score,
        "feedback":    feedback_html,
        "resume_name": resume_name,
        "jd_name":     jd_name,
        "benchmark":   benchmark,
    })


@router.get("/fine_tune")
async def fine_tune(request: Request):
    resume_text, jd_text = get_session_texts(request)
    if not (resume_text and jd_text):
        flash(request, "Please upload both a resume and a job description before fine-tuning.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        # Same pattern: run the blocking OpenAI call in a thread pool so the
        # event loop is not frozen while waiting for the API response.
        optimized = await asyncio.to_thread(fine_tune_resume, resume_text, jd_text)
    except Exception as e:
        flash(request, f"Error optimising resume: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    # optimized_html = mistune.create_markdown()(optimized)
    optimized_html = mistune.create_markdown(plugins=["table"])(optimized)
    resume_name, jd_name = get_file_names(request)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    tmp.close()
    save_as_docx(optimized, tmp.name)
    request.session['download_resume_path'] = tmp.name

    return templates.TemplateResponse(request, "optimization_report.html", {
        "optimized_resume": optimized_html,
        "report":           "Fine-tuning complete. Resume is now ATS-friendly.",
        "download_path":    tmp.name,
        "resume_name":      resume_name,
        "jd_name":          jd_name,
    })


@router.get("/generate_cover_letter")
async def generate_cover_letter_route(request: Request):
    resume_text, jd_text = get_session_texts(request)
    if not (resume_text and jd_text):
        flash(request, "Please upload both a resume and a job description before generating a cover letter.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        cover_letter_md = await asyncio.to_thread(generate_cover_letter, resume_text, jd_text)
    except Exception as e:
        flash(request, f"Error generating cover letter: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    # cover_letter_html = mistune.create_markdown()(cover_letter_md)
    cover_letter_html = mistune.create_markdown(plugins=["table"])(cover_letter_md)
    resume_name, jd_name = get_file_names(request)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    tmp.close()
    save_as_docx(cover_letter_md, tmp.name)
    request.session['download_cover_letter_path'] = tmp.name

    return templates.TemplateResponse(request, "cover_letter.html", {
        "cover_letter":  cover_letter_html,
        "download_path": tmp.name,
        "resume_name":   resume_name,
        "jd_name":       jd_name,
    })


@router.get("/analyze_job_posting")
async def analyze_job_posting_route(request: Request):
    _, jd_text = get_session_texts(request)
    if not jd_text:
        flash(request, "Please upload a job description before analyzing it.", "error")
        return RedirectResponse(url="/upload", status_code=303)

    try:
        analysis = await asyncio.to_thread(analyze_job_posting, jd_text)
    except Exception as e:
        flash(request, f"Error analysing job posting: {e}", "error")
        return RedirectResponse(url="/upload", status_code=303)

    # analysis_html = mistune.create_markdown()(analysis)
    analysis_html = mistune.create_markdown(plugins=["table"])(analysis)
    resume_name, jd_name = get_file_names(request)

    return templates.TemplateResponse(request, "job_analysis.html", {
        "analysis":    analysis_html,
        "resume_name": resume_name,
        "jd_name":     jd_name,
    })


@router.get("/dashboard")
async def dashboard(request: Request):
    """
    Market Intelligence dashboard.

    Option 1: skill_trends uses Delta time travel to show which keywords
              are rising or falling compared to 7 days ago.
    Option 2: remote_breakdown and experience_dist use the new columns
              added to job_submissions via Delta schema evolution.
    All new calls fail gracefully (return [] if Databricks is unavailable).
    """
    return templates.TemplateResponse(request, "dashboard.html", {
        "total":            get_total_submissions(),
        "tech_skills":      get_top_technical_skills(),
        "soft_skills":      get_top_soft_skills(),
        "keywords":         get_top_keywords(),
        "industries":       get_industry_breakdown(),
        "seniority":        get_seniority_breakdown(),
        "skill_trends":     get_skill_trend_comparison(days_back=7),
        "remote_breakdown": get_remote_breakdown(),
        "experience_dist":  get_experience_distribution(),
    })


@router.get("/download_report")
async def download_report(path: str):
    """Download the optimised resume as a .docx file."""
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
    if not (resume_path and jd_path):
        flash(request, "Please upload your resume and job description first.", "error")
        return RedirectResponse(url="/upload", status_code=303)
    resume_name, jd_name = get_file_names(request)
    return templates.TemplateResponse(request, "tools.html", {
        "resume_name": resume_name,
        "jd_name":     jd_name,
    })


@router.get("/clear")
async def clear_session(request: Request):
    request.session.clear()
    flash(request, "Session cleared.", "success")
    return RedirectResponse(url="/upload", status_code=303)
