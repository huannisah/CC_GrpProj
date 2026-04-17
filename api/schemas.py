"""
schemas.py

Pydantic request/response models for all REST API endpoints.
FastAPI uses these for automatic validation and Swagger documentation.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ── Request Models ─────────────────────────────────────────────────────────────

class ATSScoreRequest(BaseModel):
    resume_text: str = Field(..., description="Plain text content of the resume")
    jd_text: str = Field(..., description="Plain text content of the job description")

    model_config = {
        "json_schema_extra": {
            "example": {
                "resume_text": "Jane Doe\nSoftware Engineer with 5 years experience in Python...",
                "jd_text": "We are looking for a Senior Python Engineer with FastAPI experience..."
            }
        }
    }


class FineTuneRequest(BaseModel):
    resume_text: str = Field(..., description="Plain text content of the resume")
    jd_text: str = Field(..., description="Plain text content of the job description")


class CoverLetterRequest(BaseModel):
    resume_text: str = Field(..., description="Plain text content of the resume")
    jd_text: str = Field(..., description="Plain text content of the job description")


class JobAnalysisRequest(BaseModel):
    jd_text: str = Field(..., description="Plain text content of the job description")


# ── Response Models ────────────────────────────────────────────────────────────

class ATSScoreResponse(BaseModel):
    score: float = Field(..., description="ATS compatibility score from 1.0 to 10.0 in 0.5 increments")
    feedback: str = Field(..., description="Detailed feedback on the resume")

    # Option 4: crowd-sourced benchmark fields.
    # All Optional — they are None if Databricks has no data yet.
    # Over time, as more users run ATS scoring, these become increasingly meaningful.
    benchmark_avg: Optional[float] = Field(
        None,
        description="Global average ATS score across all Databricks submissions"
    )
    benchmark_percentile: Optional[int] = Field(
        None,
        description="Percentage of past submissions this score beats (0–100)"
    )
    benchmark_total: Optional[int] = Field(
        None,
        description="Total number of ATS scores recorded in Databricks"
    )


class FineTuneResponse(BaseModel):
    optimized_resume: str = Field(..., description="ATS-optimised version of the resume")


class CoverLetterResponse(BaseModel):
    cover_letter: str = Field(..., description="Generated personalised cover letter")


class JobAnalysisResponse(BaseModel):
    analysis: str = Field(..., description="Detailed job posting analysis and candidate advice")


# ── Market Trends sub-models ──────────────────────────────────────────────────

class SkillItem(BaseModel):
    skill: str
    count: int


class KeywordItem(BaseModel):
    keyword: str
    count: int


class IndustryItem(BaseModel):
    industry: str
    count: int


class SeniorityItem(BaseModel):
    seniority: str
    count: int

class RemoteItem(BaseModel):
    remote_type: str = Field(..., description="e.g. 'Remote', 'Hybrid', 'On-site', 'Not specified'")
    count: int

class ExperienceItem(BaseModel):
    seniority: str = Field(..., description="e.g. 'Entry', 'Mid', 'Senior', 'Lead'")
    avg_years: float = Field(..., description="Average minimum years of experience required")
    count: int = Field(..., description="Number of submissions in this seniority bucket")

class SkillTrendItem(BaseModel):
    keyword: str
    current_count: int = Field(..., description="Current occurrence count in keyword_counts table")
    past_count: int = Field(..., description="Count from Delta snapshot N days ago (0 if table too new)")
    change: int = Field(..., description="current_count minus past_count")
    pct_change: float = Field(..., description="Percentage change from past to current")
    direction: str = Field(
        ...,
        description="'up' (rising), 'down' (falling), 'stable', or 'new' (no historical baseline)"
    )


class MarketTrendsResponse(BaseModel):
    total_submissions: int = Field(..., description="Total number of job descriptions analysed")
    top_technical_skills: list[SkillItem]
    top_soft_skills: list[SkillItem]
    top_keywords: list[KeywordItem]
    industry_breakdown: list[IndustryItem]
    seniority_breakdown: list[SeniorityItem]

    remote_breakdown: list[RemoteItem] = Field(
        default=[],
        description="Distribution of remote work types across all submitted job descriptions"
    )
    experience_distribution: list[ExperienceItem] = Field(
        default=[],
        description="Average required years of experience by seniority level"
    )

    skill_trends: list[SkillTrendItem] = Field(
        default=[],
        description="Keyword trend comparison using Delta Lake time travel (7-day window)"
    )
