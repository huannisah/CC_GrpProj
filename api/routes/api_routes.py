"""
api_routes.py

All REST API endpoints under /api/v1/.
Each endpoint is stateless — resume text and job description are passed
in the request body every time. No server-side session state.
"""

from fastapi import APIRouter, HTTPException
from api.schemas import (
    ATSScoreRequest, ATSScoreResponse,
    FineTuneRequest, FineTuneResponse,
    CoverLetterRequest, CoverLetterResponse,
    JobAnalysisRequest, JobAnalysisResponse,
    MarketTrendsResponse, SkillItem, KeywordItem, IndustryItem, SeniorityItem
)
from utils.openai_service import (
    get_ats_score, fine_tune_resume,
    generate_cover_letter, analyze_job_posting
)
from utils.analytics_service import (
    get_top_technical_skills, get_top_soft_skills,
    get_top_keywords, get_industry_breakdown,
    get_seniority_breakdown, get_total_submissions
)

router = APIRouter()


@router.post(
    "/ats-score",
    response_model=ATSScoreResponse,
    summary="Score a resume against a job description",
    description=(
        "Analyses the resume against the job description using ATS evaluation rules. "
        "Returns a score from 1–10 and detailed improvement feedback."
    )
)
async def api_ats_score(body: ATSScoreRequest):
    try:
        score, feedback = get_ats_score(body.resume_text, body.jd_text)
        return ATSScoreResponse(score=score, feedback=feedback)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/fine-tune",
    response_model=FineTuneResponse,
    summary="Optimise a resume for ATS compatibility",
    description=(
        "Rewrites the resume to maximise ATS compatibility based on the job description. "
        "Applies formatting rules, keyword alignment, and action verb enhancement."
    )
)
async def api_fine_tune(body: FineTuneRequest):
    try:
        optimized = fine_tune_resume(body.resume_text, body.jd_text)
        return FineTuneResponse(optimized_resume=optimized)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/cover-letter",
    response_model=CoverLetterResponse,
    summary="Generate a personalised cover letter",
    description=(
        "Generates a tailored cover letter based solely on the provided resume and job description. "
        "Follows best-practice cover letter guidelines for ATS compatibility and human appeal."
    )
)
async def api_cover_letter(body: CoverLetterRequest):
    try:
        letter = generate_cover_letter(body.resume_text, body.jd_text)
        return CoverLetterResponse(cover_letter=letter)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/job-analysis",
    response_model=JobAnalysisResponse,
    summary="Analyse a job posting",
    description=(
        "Breaks down a job description into required skills, cultural signals, "
        "ATS keywords, seniority level, and candidate advice."
    )
)
async def api_job_analysis(body: JobAnalysisRequest):
    try:
        analysis = analyze_job_posting(body.jd_text)
        return JobAnalysisResponse(analysis=analysis)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/market-trends",
    response_model=MarketTrendsResponse,
    summary="Get aggregated job market intelligence",
    description=(
        "Returns anonymised, crowdsourced job market signals derived from all job descriptions "
        "ever submitted to CaaS. No personal data is stored — only aggregate skill, keyword, "
        "industry, and seniority counts. Free to use, no authentication required."
    )
)
async def api_market_trends():
    """
    Returns market trend data from Databricks.
    If Databricks is unreachable, returns empty lists gracefully (no 500 error).
    """
    return MarketTrendsResponse(
        total_submissions=get_total_submissions(),
        top_technical_skills=[SkillItem(**s) for s in get_top_technical_skills()],
        top_soft_skills=[SkillItem(**s) for s in get_top_soft_skills()],
        top_keywords=[KeywordItem(**k) for k in get_top_keywords()],
        industry_breakdown=[IndustryItem(**i) for i in get_industry_breakdown()],
        seniority_breakdown=[SeniorityItem(**s) for s in get_seniority_breakdown()],
    )
