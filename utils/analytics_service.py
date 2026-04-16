"""
analytics_service.py

Dual-utility crowdsourcing layer for CareerAI-as-a-Service.

Every job description submitted is anonymously mined for skill/keyword signals,
which are aggregated into Databricks Delta Tables.

IMPORTANT: Every function is wrapped in try/except so the app continues to work
even if Databricks is unreachable (graceful degradation).
"""

from config import Config
from datetime import datetime


def _has_databricks_config() -> bool:
    """Check whether all required Databricks environment variables are set."""
    return bool(
        Config.DATABRICKS_HOST
        and Config.DATABRICKS_TOKEN
        and Config.DATABRICKS_HTTP_PATH
    )


def get_connection():
    """
    Open a Databricks SQL connection.
    Raises an exception if credentials are missing or connection fails.
    """
    from databricks import sql as databricks_sql

    return databricks_sql.connect(
        server_hostname=Config.DATABRICKS_HOST.replace("https://", ""),
        http_path=Config.DATABRICKS_HTTP_PATH,
        access_token=Config.DATABRICKS_TOKEN,
    )


def init_db():
    """
    Create Delta Tables in Databricks if they don't exist.
    Called once at app startup. If Databricks is unreachable, prints a warning
    and lets the app start anyway (degraded mode — market trends will be empty).
    """
    if not _has_databricks_config():
        print("WARNING: Databricks credentials not configured. Market Trends will be unavailable.")
        return

    try:
        conn = get_connection()
        cursor = conn.cursor()

        catalog = Config.DATABRICKS_CATALOG
        schema = Config.DATABRICKS_SCHEMA

        # Ensure schema exists
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

        # Job submissions table — stores anonymised metadata per submission
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.job_submissions (
                id          BIGINT GENERATED ALWAYS AS IDENTITY,
                job_title   STRING,
                industry    STRING,
                seniority   STRING,
                submitted_at STRING
            )
        """)

        # Skill counts table — aggregated counts of technical + soft skills
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.skill_counts (
                skill       STRING,
                skill_type  STRING,
                count       BIGINT
            )
        """)

        # Keyword counts table — aggregated counts of job description keywords
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.keyword_counts (
                keyword STRING,
                count   BIGINT
            )
        """)

        cursor.close()
        conn.close()
        print("Databricks Delta Tables initialised successfully.")
    except Exception as e:
        print(f"WARNING: Could not initialise Databricks tables: {e}")
        print("The app will continue without Market Trends functionality.")


def record_insights(insights: dict):
    """
    Write anonymised insights from a job description into Delta Tables.
    Uses MERGE (upsert) for skill and keyword counts.
    Fails silently if Databricks is unreachable.
    """
    if not insights or not _has_databricks_config():
        return

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cat = Config.DATABRICKS_CATALOG
        sch = Config.DATABRICKS_SCHEMA

        # Insert job submission record (no PII — only title/industry/seniority)
        cursor.execute(
            f"INSERT INTO {cat}.{sch}.job_submissions (job_title, industry, seniority, submitted_at) VALUES (?, ?, ?, ?)",
            (
                insights.get("job_title", "Unknown"),
                insights.get("industry", "Unknown"),
                insights.get("seniority_level", "Unknown"),
                datetime.utcnow().isoformat(),
            )
        )

        # Upsert technical skills using MERGE
        for skill in insights.get("technical_skills", []):
            skill = skill.strip().lower()
            if skill:
                cursor.execute(f"""
                    MERGE INTO {cat}.{sch}.skill_counts AS target
                    USING (SELECT '{skill}' AS skill, 'technical' AS skill_type) AS source
                    ON target.skill = source.skill AND target.skill_type = source.skill_type
                    WHEN MATCHED THEN UPDATE SET target.count = target.count + 1
                    WHEN NOT MATCHED THEN INSERT (skill, skill_type, count) VALUES ('{skill}', 'technical', 1)
                """)

        # Upsert soft skills using MERGE
        for skill in insights.get("soft_skills", []):
            skill = skill.strip().lower()
            if skill:
                cursor.execute(f"""
                    MERGE INTO {cat}.{sch}.skill_counts AS target
                    USING (SELECT '{skill}' AS skill, 'soft' AS skill_type) AS source
                    ON target.skill = source.skill AND target.skill_type = source.skill_type
                    WHEN MATCHED THEN UPDATE SET target.count = target.count + 1
                    WHEN NOT MATCHED THEN INSERT (skill, skill_type, count) VALUES ('{skill}', 'soft', 1)
                """)

        # Upsert keywords using MERGE
        for kw in insights.get("keywords", []):
            kw = kw.strip().lower()
            if kw:
                cursor.execute(f"""
                    MERGE INTO {cat}.{sch}.keyword_counts AS target
                    USING (SELECT '{kw}' AS keyword) AS source
                    ON target.keyword = source.keyword
                    WHEN MATCHED THEN UPDATE SET target.count = target.count + 1
                    WHEN NOT MATCHED THEN INSERT (keyword, count) VALUES ('{kw}', 1)
                """)

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"WARNING: Failed to record insights to Databricks: {e}")


def get_top_technical_skills(limit: int = 15) -> list[dict]:
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT skill, count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.skill_counts
            WHERE skill_type = 'technical'
            ORDER BY count DESC
            LIMIT {limit}
        """)
        rows = [{"skill": row[0], "count": row[1]} for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch technical skills: {e}")
        return []


def get_top_soft_skills(limit: int = 10) -> list[dict]:
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT skill, count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.skill_counts
            WHERE skill_type = 'soft'
            ORDER BY count DESC
            LIMIT {limit}
        """)
        rows = [{"skill": row[0], "count": row[1]} for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch soft skills: {e}")
        return []


def get_top_keywords(limit: int = 20) -> list[dict]:
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT keyword, count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.keyword_counts
            ORDER BY count DESC
            LIMIT {limit}
        """)
        rows = [{"keyword": row[0], "count": row[1]} for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch keywords: {e}")
        return []


def get_industry_breakdown() -> list[dict]:
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT industry, COUNT(*) as count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.job_submissions
            GROUP BY industry
            ORDER BY count DESC
            LIMIT 10
        """)
        rows = [{"industry": row[0], "count": row[1]} for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch industry breakdown: {e}")
        return []


def get_seniority_breakdown() -> list[dict]:
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT seniority, COUNT(*) as count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.job_submissions
            GROUP BY seniority
            ORDER BY count DESC
        """)
        rows = [{"seniority": row[0], "count": row[1]} for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch seniority breakdown: {e}")
        return []


def get_total_submissions() -> int:
    if not _has_databricks_config():
        return 0
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT COUNT(*)
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.job_submissions
        """)
        total = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return total
    except Exception as e:
        print(f"WARNING: Could not fetch total submissions: {e}")
        return 0
