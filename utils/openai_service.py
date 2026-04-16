"""
openai_service.py

All OpenAI API interactions live here.
Routes call these functions — they never touch the OpenAI client directly.
"""

import os
import re
import json
from typing import Optional
from openai import OpenAI
from config import Config

# Use the model specified in config (defaults to gpt-4o-mini)
CHAT_MODEL = Config.OPENAI_MODEL

# Lazy-initialised OpenAI client — created on first use so the app can
# start even if OPENAI_API_KEY is not yet set (e.g. during build on Render)
_client = None


def _get_client() -> OpenAI:
    """Return the OpenAI client, creating it on first use."""
    global _client
    if _client is None:
        api_key = Config.OPENAI_API_KEY
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set. "
                "Please add it to your .env file or Render environment variables."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def load_prompt(filename: str) -> str:
    """
    Load a prompt rule file from the prompts/ directory.
    Uses os.path to build the path relative to the project root.
    """
    # Build path relative to this file's location → ../prompts/filename
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    filepath = os.path.join(base_dir, 'prompts', filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def get_ats_score(resume_text: str, job_description_text: str) -> tuple:
    """
    Score a resume against a job description in increments of 0.5 (1.0–10.0).
    Returns: (score: float, feedback: str)
    """
    ats_prompt = load_prompt('ats_score_rule.txt')
    try:
        response = _get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an ATS score evaluator. Analyze the resume against the job description. "
                        "Provide a score from 1.0 to 10.0 in increments of 0.5 (e.g., 5.0, 6.5, 8.0) "
                        "based on how well the resume matches the job description, "
                        "along with detailed feedback on areas that need improvement. "
                        "Always give your score on the first line in the format 'ATS Score: X.X/10', "
                        "then provide feedback in subsequent lines."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Scoring Rules:\n{ats_prompt}\n\n"
                        f"Resume:\n{resume_text}\n\n"
                        f"Job Description:\n{job_description_text}\n\n"
                        "Please provide an ATS score and feedback. "
                        "Score must be in increments of 0.5 and in format 'ATS Score: X.X/10'."
                    )
                }
            ],
            temperature=0.3,
        )
        score_feedback = response.choices[0].message.content
        score, feedback = _parse_score_feedback(score_feedback)
        return score, feedback
    except Exception as e:
        raise RuntimeError(f"OpenAI API error during ATS scoring: {e}")


def _parse_score_feedback(score_feedback: str) -> tuple:
    """
    Extract a float score and feedback from the AI response.
    Handles both X/10 and X.X/10 patterns.
    Rounds to nearest 0.5 to enforce the increment rule even if the
    model occasionally returns a non-0.5-aligned value.
    """
    # Match decimal or integer score: e.g. "7.5/10" or "7/10"
    match = re.search(r'(\d+(?:\.\d+)?)\s*/\s*10', score_feedback)
    if match:
        raw_score = float(match.group(1))
        # Round to nearest 0.5 and clamp to [1.0, 10.0]
        score = round(round(raw_score * 2) / 2, 1)
        score = max(1.0, min(10.0, score))
    else:
        score = 0.0

    # Remove the score header line from feedback to avoid duplication
    lines = score_feedback.strip().splitlines()
    feedback_lines = [
        line for line in lines
        if not re.match(r'^\s*ATS\s+Score\s*:', line, re.IGNORECASE)
    ]
    feedback = '\n'.join(feedback_lines).strip()

    return score, feedback



def fine_tune_resume(resume_text: str, job_description_text: str) -> str:
    """Rewrite the resume to be ATS-optimised for the given job description."""
    optimization_prompt = load_prompt('resume_optimization_rule.txt')
    try:
        response = _get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a resume optimization assistant. Fine-tune the resume to make it "
                        "ATS-friendly based on the job description. Make necessary adjustments and "
                        "provide an updated resume."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Rules for fine-tuning:\n{optimization_prompt}\n\n"
                        f"Resume:\n{resume_text}\n\n"
                        f"Job Description:\n{job_description_text}\n\n"
                        "Please fine-tune the resume."
                    )
                }
            ],
            temperature=0.5,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"OpenAI API error during resume optimisation: {e}")


def generate_cover_letter(resume_text: str, job_description_text: str) -> str:
    """Generate a tailored cover letter from resume + job description."""
    cover_letter_prompt = load_prompt('cover_letter_rule.txt')
    try:
        response = _get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a cover letter generator. Create a personalized cover letter "
                        "based ONLY on the resume and job description provided. "
                        "Do not add content that is not supported by the provided documents."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Rules for writing cover letter:\n{cover_letter_prompt}\n\n"
                        f"Resume:\n{resume_text}\n\n"
                        f"Job Description:\n{job_description_text}\n\n"
                        "Please generate a cover letter."
                    )
                }
            ],
            temperature=0.5,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"OpenAI API error during cover letter generation: {e}")


def analyze_job_posting(job_description_text: str) -> str:
    """Analyse a job posting and return structured insights."""
    analysis_prompt = load_prompt('job_analysis_rule.txt')
    try:
        response = _get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a job posting analysis assistant. Analyze the job description "
                        "and provide insights and suggestions for candidates."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Analysis Rules:\n{analysis_prompt}\n\n"
                        f"Job Description:\n{job_description_text}\n\n"
                        "Please provide an analysis."
                    )
                }
            ],
            temperature=0.5,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"OpenAI API error during job analysis: {e}")


def extract_market_insights(job_description_text: str) -> Optional[dict]:
    """
    Extract structured skill/keyword data from a job description
    for anonymous aggregation into the market trends dashboard.
    Returns a dict with keys: job_title, industry, seniority_level,
    technical_skills, soft_skills, keywords.
    Returns None if extraction fails.
    """
    try:
        response = _get_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a job market data extractor. Extract structured information "
                        "from job descriptions for market trend analysis. "
                        "Respond ONLY with a valid JSON object, no markdown, no explanation."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Job Description:\n{job_description_text}\n\n"
                        "Extract the following and return as JSON only:\n"
                        "{\n"
                        '  "job_title": "string (best guess at job title)",\n'
                        '  "industry": "string (e.g. Tech, Finance, Healthcare)",\n'
                        '  "seniority_level": "string (Entry/Mid/Senior/Lead/Executive)",\n'
                        '  "technical_skills": ["list", "of", "technical", "skills"],\n'
                        '  "soft_skills": ["list", "of", "soft", "skills"],\n'
                        '  "keywords": ["top", "10", "keywords"]\n'
                        "}"
                    )
                }
            ],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if the model wraps its response in ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        print("Warning: Could not parse market insights JSON from OpenAI response")
        return None
    except Exception as e:
        print(f"Warning: Market insights extraction failed: {e}")
        return None
