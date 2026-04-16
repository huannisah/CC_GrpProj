"""
utils/analytics_service.py

Dual-utility crowdsourcing layer for CareerAI-as-a-Service.

Every job description submitted is anonymously mined for skill/keyword signals,
which are aggregated into Databricks Delta Tables.

NEW in this version (Options 1, 2, 4):
  - Option 4: ats_scores table + record_ats_score() + get_ats_benchmark()
              Users see "You beat 73% of submissions" on the ATS score page.
  - Option 1: get_skill_trend_comparison() uses Delta Lake TIME TRAVEL to compare
              current keyword counts against a snapshot from N days ago.
              This is a Delta-exclusive feature — standard SQL databases cannot do this.
  - Option 2: job_submissions now stores remote_type, required_years, salary_range.
              Schema evolution via ALTER TABLE ADD COLUMN (one try/except per column).
              New functions: get_remote_breakdown(), get_experience_distribution().

IMPORTANT: Every function is wrapped in try/except so the app continues to work
even if Databricks is unreachable (graceful degradation).
"""

from config import Config
from datetime import datetime, timedelta


# ── Connection helpers ─────────────────────────────────────────────────────────

def _has_databricks_config() -> bool:
    """Check whether all required Databricks environment variables are set."""
    return bool(
        Config.DATABRICKS_HOST
        and Config.DATABRICKS_TOKEN
        and Config.DATABRICKS_HTTP_PATH
    )

# AFTER — 10 second connection timeout so a slow Databricks cluster
# never hangs a user request for more than 10 seconds
def get_connection():
    from databricks import sql as databricks_sql
    return databricks_sql.connect(
        server_hostname=Config.DATABRICKS_HOST.replace("https://", ""),
        http_path=Config.DATABRICKS_HTTP_PATH,
        access_token=Config.DATABRICKS_TOKEN,
        _socket_timeout=10,   # ← fail fast if Databricks is unreachable
    )

# ── Database initialisation ────────────────────────────────────────────────────

def init_db():
    """
    Create Delta Tables in Databricks if they don't exist.
    Called once at app startup. If Databricks is unreachable, prints a warning
    and lets the app start anyway (degraded mode — market trends will be empty).

    Delta-specific features used here:
      1. USING DELTA — ensures tables are Delta format (enables time travel, MERGE, etc.)
      2. Schema evolution via ALTER TABLE ADD COLUMN — one try/except per column.
         We intentionally do NOT use "IF NOT EXISTS" in the ALTER TABLE statement
         because Databricks Free Edition SQL warehouses run an older runtime that
         does not support that clause. Instead we attempt each ALTER and silently
         skip it if the column already exists (the error is caught and ignored).
    """
    if not _has_databricks_config():
        print("WARNING: Databricks credentials not configured. Market Trends will be unavailable.")
        return

    try:
        conn = get_connection()
        cursor = conn.cursor()

        catalog = Config.DATABRICKS_CATALOG
        schema = Config.DATABRICKS_SCHEMA

        # Ensure schema (namespace) exists
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

        # ── Original tables ────────────────────────────────────────────────────

        # Job submissions: one row per job description uploaded to the platform.
        # No PII is stored — only anonymous metadata extracted by AI.
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.job_submissions (
                id           BIGINT GENERATED ALWAYS AS IDENTITY,
                job_title    STRING,
                industry     STRING,
                seniority    STRING,
                submitted_at STRING
            )
            USING DELTA
        """)

        # Skill counts: aggregated occurrence counts, updated via MERGE (upsert).
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.skill_counts (
                skill       STRING,
                skill_type  STRING,
                count       BIGINT
            )
            USING DELTA
        """)

        # Keyword counts: same pattern for JD keywords.
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.keyword_counts (
                keyword STRING,
                count   BIGINT
            )
            USING DELTA
        """)

        # ── Option 4: ATS score table ──────────────────────────────────────────
        # Each time a user runs ATS scoring, we record the score here.
        # Over time this builds a distribution we can use for percentile ranking.
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {catalog}.{schema}.ats_scores (
                score        FLOAT,
                industry     STRING,
                seniority    STRING,
                submitted_at STRING
            )
            USING DELTA
        """)

        # ── Option 2: Schema evolution for job_submissions ─────────────────────
        # We want to add three new columns to the existing job_submissions table.
        #
        # WHY NO "IF NOT EXISTS":
        #   The standard Databricks syntax would be:
        #       ALTER TABLE t ADD COLUMN IF NOT EXISTS col STRING
        #   However, Databricks Free Edition runs an older SQL warehouse runtime
        #   that raises a PARSE_SYNTAX_ERROR on that clause. Instead, we attempt
        #   each ALTER TABLE individually. If the column already exists, Databricks
        #   raises an AnalysisException which we catch and silently discard.
        #   This achieves the same idempotent result — the column is present after
        #   init_db() runs, regardless of whether it was just created or already existed.
        new_columns = [
            ("remote_type",    "STRING"),   # e.g. "Remote", "Hybrid", "On-site"
            ("required_years", "INT"),      # minimum years of experience required
            ("salary_range",   "STRING"),   # e.g. "$80,000 - $120,000" or NULL
        ]
        for col_name, col_type in new_columns:
            try:
                # Plain ADD COLUMN — no IF NOT EXISTS (not supported on Free tier)
                cursor.execute(
                    f"ALTER TABLE {catalog}.{schema}.job_submissions "
                    f"ADD COLUMN {col_name} {col_type}"
                )
            except Exception:
                # Column already exists → AnalysisException is raised and swallowed.
                # Any other error is also swallowed here so startup is never blocked.
                pass

        cursor.close()
        conn.close()
        print("Databricks Delta Tables initialised successfully.")
    except Exception as e:
        print(f"WARNING: Could not initialise Databricks tables: {e}")
        print("The app will continue without Market Trends functionality.")


# ── Option 4: ATS Score Benchmarking ──────────────────────────────────────────

def record_ats_score(score: float, industry: str = "Unknown", seniority: str = "Unknown") -> None:
    """
    Record an ATS score submission into the ats_scores Delta table.

    Called every time a user gets an ATS score — building a crowd-sourced
    distribution that makes percentile ranking meaningful over time.

    Why this is valuable: the raw score (e.g. 7.5/10) has no context without
    knowing what everyone else scores. After 100+ submissions, a user can see
    "you beat 80% of resumes submitted for Tech roles" — that's actionable.

    Fails silently so it NEVER blocks or delays the ATS score response to the user.
    """
    if not _has_databricks_config():
        return
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cat = Config.DATABRICKS_CATALOG
        sch = Config.DATABRICKS_SCHEMA
        cursor.execute(
            f"INSERT INTO {cat}.{sch}.ats_scores (score, industry, seniority, submitted_at) "
            f"VALUES (?, ?, ?, ?)",
            (score, industry, seniority, datetime.utcnow().isoformat()),
        )
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"WARNING: Could not record ATS score: {e}")


def get_ats_benchmark(user_score: float, industry: str = "Unknown") -> dict:
    """
    Compare the user's ATS score against all previously recorded scores.

    Returns a dict with:
      - available: bool — False if Databricks is down or no data yet
      - avg_score: float — global average across all submissions
      - total_count: int — how many scores are in the dataset
      - percentile: int — % of past submissions the user's score BEATS
      - top_score: float — highest score ever recorded

    The percentile is computed with a simple COUNT(score < user_score) / COUNT(*).
    This is a crowd-sourced benchmark that gets more accurate with each submission.
    """
    if not _has_databricks_config():
        return {"available": False}
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cat = Config.DATABRICKS_CATALOG
        sch = Config.DATABRICKS_SCHEMA

        # Single query: compute all benchmark metrics in one round trip.
        # CASE WHEN score < user_score counts how many submissions this user beats.
        cursor.execute(f"""
            SELECT
                ROUND(AVG(score), 1)                                                AS avg_score,
                COUNT(*)                                                            AS total_count,
                ROUND(
                    100.0 * SUM(CASE WHEN score < {user_score} THEN 1 ELSE 0 END)
                    / COUNT(*),
                    0
                )                                                                   AS percentile_rank,
                ROUND(MAX(score), 1)                                                AS top_score
            FROM {cat}.{sch}.ats_scores
        """)
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row or row[1] == 0:
            # Table is empty — no benchmark data yet
            return {"available": False}

        return {
            "available":   True,
            "avg_score":   float(row[0]) if row[0] is not None else 0.0,
            "total_count": int(row[1]),
            "percentile":  int(row[2]) if row[2] is not None else 0,
            "top_score":   float(row[3]) if row[3] is not None else 0.0,
        }
    except Exception as e:
        print(f"WARNING: Could not fetch ATS benchmark: {e}")
        return {"available": False}


# ── Option 1: Delta Lake Time Travel ──────────────────────────────────────────

def get_skill_trend_comparison(days_back: int = 7) -> list[dict]:
    """
    Compare current keyword counts against a historical Delta Lake snapshot.

    HOW DELTA TIME TRAVEL WORKS:
      Delta Lake records every write (INSERT, UPDATE, MERGE) as a new "version"
      in its transaction log. The syntax:
          SELECT * FROM table TIMESTAMP AS OF '2024-01-01 00:00:00'
      queries the table exactly as it looked at that timestamp — without any
      separate backup or ETL job. Standard SQL databases cannot do this.

    This function returns the top keywords and shows whether each one is
    rising, falling, or new compared to N days ago.

    NOTE: If the table is newer than days_back (e.g. you just created it),
    the historical query will fail gracefully and direction will be "new"
    for all keywords — meaning all growth happened within the lookback window.
    This still works correctly; the trend feature becomes more useful over time.
    """
    if not _has_databricks_config():
        return []

    cat = Config.DATABRICKS_CATALOG
    sch = Config.DATABRICKS_SCHEMA
    # Compute the lookback timestamp as a string in Databricks SQL format
    lookback_ts = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Step 1: Get the current top keywords
        cursor.execute(f"""
            SELECT keyword, count
            FROM {cat}.{sch}.keyword_counts
            ORDER BY count DESC
            LIMIT 15
        """)
        current = {row[0]: int(row[1]) for row in cursor.fetchall()}

        if not current:
            cursor.close()
            conn.close()
            return []

        # Step 2: Query the SAME table at a past timestamp using Delta time travel.
        # We only fetch keywords that currently exist (no point fetching extinct keywords).
        # The IN clause is built from current keywords — safe since they come from our DB,
        # not from user input.
        keyword_list = ", ".join([f"'{k.replace(chr(39), '')}'" for k in current.keys()])
        historical = {}
        try:
            cursor.execute(f"""
                SELECT keyword, count
                FROM {cat}.{sch}.keyword_counts TIMESTAMP AS OF '{lookback_ts}'
                WHERE keyword IN ({keyword_list})
            """)
            historical = {row[0]: int(row[1]) for row in cursor.fetchall()}
        except Exception as te:
            # This is expected when the table is newer than the lookback window.
            # We log it and continue — all keywords will be marked "new".
            print(f"Delta time travel unavailable (table may be < {days_back} days old): {te}")

        cursor.close()
        conn.close()

        # Step 3: Compute trend metrics for each keyword
        result = []
        for keyword, current_count in current.items():
            past_count = historical.get(keyword, 0)
            change = current_count - past_count

            if past_count == 0:
                direction = "new"       # No historical data — this keyword appeared recently
                pct_change = 100.0
            elif change > 0:
                direction = "up"
                pct_change = round(change / past_count * 100, 1)
            elif change < 0:
                direction = "down"      # Shouldn't happen with additive counts, but handled
                pct_change = round(change / past_count * 100, 1)
            else:
                direction = "stable"
                pct_change = 0.0

            result.append({
                "keyword":       keyword,
                "current_count": current_count,
                "past_count":    past_count,
                "change":        change,
                "pct_change":    pct_change,
                "direction":     direction,
            })

        # Sort by biggest absolute change first — most dynamic keywords at top
        result.sort(key=lambda x: abs(x["change"]), reverse=True)
        return result

    except Exception as e:
        print(f"WARNING: Could not fetch skill trends: {e}")
        return []


# ── Option 2: Richer analytics queries ────────────────────────────────────────

def get_remote_breakdown() -> list[dict]:
    """
    Group job submissions by remote work type.
    Uses the remote_type column added via Delta schema evolution in init_db().
    Returns e.g. [{"remote_type": "Remote", "count": 23}, ...]
    """
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT
                COALESCE(remote_type, 'Not specified') AS remote_type,
                COUNT(*) AS count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.job_submissions
            WHERE remote_type IS NOT NULL
            GROUP BY remote_type
            ORDER BY count DESC
        """)
        rows = [{"remote_type": row[0], "count": int(row[1])} for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch remote breakdown: {e}")
        return []


def get_experience_distribution() -> list[dict]:
    """
    Get average required years of experience broken down by seniority level.
    This makes the "required_years" column useful — showing whether what
    companies claim (e.g. "Senior") matches what they ask for in years.
    Returns e.g. [{"seniority": "Senior", "avg_years": 4.2, "count": 15}, ...]
    """
    if not _has_databricks_config():
        return []
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT
                COALESCE(seniority, 'Unknown') AS seniority,
                ROUND(AVG(CAST(required_years AS DOUBLE)), 1) AS avg_years,
                COUNT(*) AS count
            FROM {Config.DATABRICKS_CATALOG}.{Config.DATABRICKS_SCHEMA}.job_submissions
            WHERE required_years IS NOT NULL AND required_years > 0
            GROUP BY seniority
            ORDER BY avg_years DESC
        """)
        rows = [
            {
                "seniority": row[0],
                "avg_years": float(row[1]) if row[1] is not None else 0.0,
                "count":     int(row[2]),
            }
            for row in cursor.fetchall()
        ]
        cursor.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"WARNING: Could not fetch experience distribution: {e}")
        return []


# ── Original read/write functions (unchanged logic, kept for compatibility) ────

def record_insights(insights: dict):
    """
    Write anonymised insights from a job description into Delta Tables.
    Now also writes remote_type, required_years, salary_range (Option 2).
    Uses MERGE (upsert) for skill and keyword counts so totals accumulate
    correctly across all submissions.
    Fails silently if Databricks is unreachable.
    """
    if not insights or not _has_databricks_config():
        return

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cat = Config.DATABRICKS_CATALOG
        sch = Config.DATABRICKS_SCHEMA

        # Insert job submission record.
        # Option 2 additions: remote_type, required_years, salary_range.
        # None values become SQL NULL — Databricks SQL connector handles this automatically.
        cursor.execute(
            f"""INSERT INTO {cat}.{sch}.job_submissions
                (job_title, industry, seniority, remote_type, required_years, salary_range, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                insights.get("job_title", "Unknown"),
                insights.get("industry", "Unknown"),
                insights.get("seniority_level", "Unknown"),
                insights.get("remote_type", "Not specified"),
                insights.get("required_years_min"),   # None → NULL if not found
                insights.get("salary_range"),          # None → NULL if not mentioned
                datetime.utcnow().isoformat(),
            ),
        )

        # Upsert technical skills using MERGE.
        # MERGE is a Delta Lake operation that inserts a new row if the key
        # doesn't exist, or increments the count if it does — atomically.
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

        # Upsert soft skills using MERGE (same pattern)
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
