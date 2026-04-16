"""
schemas.py

Pydantic request/response models for all REST API endpoints.
FastAPI uses these for automatic validation and Swagger documentation.
"""

from pydantic import BaseModel, Field


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


class MarketTrendsResponse(BaseModel):
    total_submissions: int = Field(..., description="Total number of job descriptions analysed")
    top_technical_skills: list[SkillItem]
    top_soft_skills: list[SkillItem]
    top_keywords: list[KeywordItem]
    industry_breakdown: list[IndustryItem]
    seniority_breakdown: list[SeniorityItem]
