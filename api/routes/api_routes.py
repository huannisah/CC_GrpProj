"""
api_routes.py

All REST API endpoints under /api/v1/.
Each endpoint is stateless — resume text and job description are passed
in the request body every time. No server-side session state.

Changes for Options 1, 2, 4:
  - /ats-score: now records each score to Databricks and returns benchmark
    fields (benchmark_avg, benchmark_percentile, benchmark_total) in the
    response. These are optional — clients that don't use them can ignore them.
  - /market-trends: now returns remote_breakdown, experience_distribution,
    and skill_trends (Delta time travel) alongside existing fields.
"""

from fastapi import APIRouter, HTTPException
from api.schemas import (
    ATSScoreRequest, ATSScoreResponse,
    FineTuneRequest, FineTuneResponse,
    CoverLetterRequest, CoverLetterResponse,
    JobAnalysisRequest, JobAnalysisResponse,
    MarketTrendsResponse, SkillItem, KeywordItem, IndustryItem, SeniorityItem,
    RemoteItem, ExperienceItem, SkillTrendItem,   # new schemas for Options 1 & 2
)
from utils.openai_service import (
    get_ats_score, fine_tune_resume,
    generate_cover_letter, analyze_job_posting
)
from utils.analytics_service import (
    get_top_technical_skills, get_top_soft_skills,
    get_top_keywords, get_industry_breakdown,
    get_seniority_breakdown, get_total_submissions,
    record_ats_score, get_ats_benchmark,          # Option 4
    get_skill_trend_comparison,                    # Option 1
    get_remote_breakdown, get_experience_distribution,  # Option 2
)

router = APIRouter()


@router.post(
    "/ats-score",
    response_model=ATSScoreResponse,
    summary="Score a resume against a job description",
    description=(
        "Analyses the resume against the job description using ATS evaluation rules. "
        "Returns a score from 1–10 and detailed improvement feedback. "
        "Also returns crowd-sourced benchmark data (percentile rank, global average) "
        "computed from all past submissions stored in Databricks."
    )
)
async def api_ats_score(body: ATSScoreRequest):
    try:
        score, feedback = get_ats_score(body.resume_text, body.jd_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Option 4: record score for crowd-sourced benchmarking.
    # Industry/seniority are unknown in the stateless API (no session), so "Unknown" is used.
    # The recording is in a try/except inside record_ats_score — it will never raise.
    record_ats_score(score, industry="Unknown", seniority="Unknown")

    # Fetch benchmark data to include in the API response.
    # benchmark["available"] == False means no data in Databricks yet — fields are None.
    benchmark = get_ats_benchmark(score)

    return ATSScoreResponse(
        score=score,
        feedback=feedback,
        benchmark_avg=benchmark.get("avg_score"),
        benchmark_percentile=benchmark.get("percentile"),
        benchmark_total=benchmark.get("total_count"),
    )


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
        "ever submitted to CaaS. Includes: top technical and soft skills, keywords, industry "
        "and seniority breakdowns, remote work breakdown, experience requirements by seniority, "
        "and keyword trend comparison using Delta Lake time travel (7-day window). "
        "No personal data is stored — only aggregate counts. Free to use, no authentication required."
    )
)
async def api_market_trends():
    """
    Returns full market intelligence data from Databricks.
    If Databricks is unreachable, all lists return empty gracefully (no 500 error).
    The skill_trends field uses Delta Lake time travel — if the table is < 7 days old,
    trend direction will be 'new' for all keywords (still valid data, just no baseline yet).
    """
    return MarketTrendsResponse(
        total_submissions=get_total_submissions(),
        top_technical_skills=[SkillItem(**s) for s in get_top_technical_skills()],
        top_soft_skills=[SkillItem(**s) for s in get_top_soft_skills()],
        top_keywords=[KeywordItem(**k) for k in get_top_keywords()],
        industry_breakdown=[IndustryItem(**i) for i in get_industry_breakdown()],
        seniority_breakdown=[SeniorityItem(**s) for s in get_seniority_breakdown()],
        # Option 2: richer submission analytics
        remote_breakdown=[RemoteItem(**r) for r in get_remote_breakdown()],
        experience_distribution=[ExperienceItem(**e) for e in get_experience_distribution()],
        # Option 1: Delta Lake time travel — keyword trend comparison
        skill_trends=[SkillTrendItem(**t) for t in get_skill_trend_comparison(days_back=7)],
    )
