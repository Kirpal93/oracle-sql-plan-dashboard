"""
Oracle SQL Plan Fluctuation Dashboard — Multi-Database Edition
Supports up to 50+ Oracle databases queried in parallel.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
# Explicitly point at the .env next to this script so it works
# regardless of which directory Streamlit is launched from.
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

import oracledb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Constants ─────────────────────────────────────────────────────────────────
DB_CONFIG_FILE = Path(__file__).parent / "db_configs.json"
MAX_WORKERS    = 20   # parallel threads for querying DBs
# Ask-the-dashboard: rows included in LLM context (keep moderate to limit latency/cost)
ASK_CONTEXT_SQL_ROWS = 100
ASK_CONTEXT_MOD_ROWS = 40
ASK_HTTP_TIMEOUT_SEC = 45
OLLAMA_BASE_URL      = "http://localhost:11434"
OLLAMA_MODEL         = "llama3"          # change to llama3.2, mistral, etc. if needed


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Oracle SQL Plan Dashboard — Multi-DB",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Main background ── */
.stApp {
    background: linear-gradient(135deg, #f0f4ff 0%, #fafbff 60%, #f5f0ff 100%);
    color: #1e2a3a;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #e8f0fe 0%, #f0eeff 100%);
    border-right: 1px solid #c5d5f5;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stTextInput label,
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span {
    color: #1e3a6e !important;
    font-size: 0.9rem !important;
    font-weight: 600 !important;
}
[data-testid="stSidebar"] input {
    background-color: #ffffff !important;
    color: #1e2a3a !important;
    border: 1.5px solid #4f6ef7 !important;
    border-radius: 6px !important;
    font-size: 0.9rem !important;
}
[data-testid="stSidebar"] input:focus {
    border-color: #3b5bd9 !important;
    box-shadow: 0 0 0 2px rgba(79,110,247,0.18) !important;
}
[data-testid="stSidebar"] input::placeholder { color: #90a4c8 !important; }
[data-testid="stSidebar"] .stMarkdown p {
    color: #1e3a6e !important; font-weight: 600 !important;
}

/* ── Cards ── */
.dash-card {
    background: #ffffff;
    border: 1px solid #dce6fb;
    border-radius: 14px;
    padding: 20px 24px;
    margin-bottom: 20px;
    box-shadow: 0 2px 12px rgba(79,110,247,0.07);
}

/* ── Section headers ── */
.section-header {
    font-size: 1.1rem; font-weight: 700; color: #2e55c8;
    letter-spacing: 0.04em; text-transform: uppercase;
    margin-bottom: 12px; border-left: 4px solid #4f6ef7; padding-left: 10px;
}

/* ── Metric tiles ── */
[data-testid="metric-container"] {
    background: #ffffff;
    border: 1px solid #dce6fb;
    border-radius: 12px;
    padding: 16px !important;
    box-shadow: 0 2px 8px rgba(79,110,247,0.08);
}
[data-testid="metric-container"] label {
    color: #5a7ab0 !important; font-size: 0.8rem !important;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #2e55c8 !important; font-size: 2rem !important; font-weight: 700 !important;
}

/* ── Divider ── */
hr { border-color: #c5d5f5 !important; }

/* ── Tables ── */
.stDataFrame { border-radius: 8px; overflow: hidden; box-shadow: 0 1px 6px rgba(79,110,247,0.07); }

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, #4f6ef7, #7c9ef8);
    color: white; border: none; border-radius: 8px;
    font-weight: 600; padding: 10px 28px; transition: all 0.2s;
    box-shadow: 0 2px 8px rgba(79,110,247,0.25);
}
.stButton > button:hover {
    background: linear-gradient(135deg, #3a56d4, #5f84f0);
    transform: translateY(-1px);
    box-shadow: 0 5px 18px rgba(79,110,247,0.35);
}

/* ── Expanders ── */
[data-testid="stExpander"] {
    background: #f5f8ff;
    border: 1px solid #dce6fb;
    border-radius: 10px;
}

/* ── Badges ── */
.badge-green { background:#e6faf0; color:#16a34a; border:1px solid #86efac;
               padding:3px 10px; border-radius:20px; font-size:0.75rem; font-weight:600; }
.badge-red   { background:#fff1f0; color:#dc2626; border:1px solid #fca5a5;
               padding:3px 10px; border-radius:20px; font-size:0.75rem; font-weight:600; }
.badge-blue  { background:#eff6ff; color:#2563eb; border:1px solid #93c5fd;
               padding:3px 10px; border-radius:20px; font-size:0.75rem; font-weight:600; }

/* ── DB cards ── */
.db-card {
    background: #ffffff;
    border: 1px solid #dce6fb;
    border-radius: 10px;
    padding: 12px 16px;
    margin-bottom: 8px;
    box-shadow: 0 1px 5px rgba(79,110,247,0.06);
}
.db-card-ok  { border-left: 4px solid #22c55e; }
.db-card-err { border-left: 4px solid #ef4444; }

/* ── Tabs ── */
[data-testid="stTabs"] [role="tab"] {
    color: #4a6fa5 !important;
    font-weight: 600;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: #2e55c8 !important;
    border-bottom: 3px solid #4f6ef7 !important;
}
</style>
""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="padding:18px 0 6px 0;">
    <span style="font-size:2rem;font-weight:800;color:#2e55c8;">🔍 Oracle SQL Plan</span>
    <span style="font-size:2rem;font-weight:800;color:#1e2a3a;"> Dashboard — Multi-DB</span>
    <p style="color:#5a7ab0;margin-top:4px;font-size:0.95rem;">
        Monitor plan instability · Compare across 50 databases · Run SQL Tuning Advisor
    </p>
</div>
""", unsafe_allow_html=True)
st.divider()


# ── DB config persistence ─────────────────────────────────────────────────────

def load_db_configs() -> list[dict]:
    if DB_CONFIG_FILE.exists():
        try:
            return json.loads(DB_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_db_configs(configs: list[dict]) -> None:
    DB_CONFIG_FILE.write_text(
        json.dumps(configs, indent=2), encoding="utf-8"
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_connection(cfg: dict):
    return oracledb.connect(
        user=cfg["user"], password=cfg["password"], dsn=cfg["dsn"]
    )


def test_connection(cfg: dict) -> tuple[bool, str]:
    try:
        with get_connection(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 'OK' FROM DUAL")
        return True, "Connected"
    except Exception as exc:
        return False, str(exc)[:120]


def query_one_db(cfg: dict, query: str, binds: dict) -> pd.DataFrame:
    """Run a query on a single DB; returns DataFrame with DATABASE column prepended."""
    with get_connection(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(query, binds)
            rows = cur.fetchall()
            cols = [c[0] for c in cur.description]
    df = pd.DataFrame(rows, columns=cols)
    df.insert(0, "DATABASE", cfg["name"])
    return df


def query_all_dbs(
    selected_cfgs: list[dict],
    query: str,
    binds: dict,
    max_workers: int = MAX_WORKERS,
) -> pd.DataFrame:
    """Query all selected DBs in parallel and return combined DataFrame."""
    frames, errors = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(query_one_db, cfg, query, binds): cfg["name"]
            for cfg in selected_cfgs
        }
        for future in as_completed(future_map):
            db_name = future_map[future]
            try:
                frames.append(future.result())
            except Exception as exc:
                errors.append({"DATABASE": db_name, "ERROR": str(exc)[:200]})

    if errors:
        st.session_state.setdefault("query_errors", []).extend(errors)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def scalar_one_db(cfg: dict, query: str, binds: dict):
    with get_connection(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(query, binds)
            row = cur.fetchone()
            if not row:
                return None
            val = row[0]
            return val.read() if hasattr(val, "read") else val


def handle_ora_error(exc: Exception, user: str = "") -> None:
    """Show a plain-language explanation followed by the Oracle error and fix SQL."""
    msg = str(exc)
    if "ORA-00942" in msg:
        st.error(
            "🔒 **Permission denied** — the database user does not have access to the "
            "required Oracle views (V$SQL, V$SQL_PLAN, V$SQLAREA). "
            "Ask your DBA to run the grant below as SYS or a privileged user."
        )
        st.code(
            f"GRANT SELECT ON V_$SQL      TO {user or '<your_user>'};\n"
            f"GRANT SELECT ON V_$SQL_PLAN TO {user or '<your_user>'};\n"
            f"GRANT SELECT ON V_$SQLAREA  TO {user or '<your_user>'};",
            language="sql",
        )
    elif "ORA-01017" in msg:
        st.error(
            "🔑 **Wrong username or password.** "
            "Double-check your credentials in the Database Manager and try again. "
            "Also check that the Oracle account is not locked."
        )
    elif "ORA-12541" in msg:
        st.error(
            "🌐 **Cannot reach the database.** The listener is not running or the "
            "host/port in your DSN is incorrect. "
            "Check that the Oracle listener is started and that port 1521 (or your port) "
            "is reachable from this machine."
        )
    elif "ORA-12154" in msg:
        st.error(
            "🌐 **DSN not recognised.** Make sure your DSN is in the format "
            "`host:port/service_name` (e.g. `myserver:1521/ORCL`). "
            "Do not use a tnsnames alias — use the direct host format."
        )
    elif "ORA-13773" in msg:
        st.error(
            "🔒 **Missing SELECT_CATALOG_ROLE.** The database user needs an extra privilege "
            "to access the cursor cache for the SQL Tuning Advisor. "
            "Ask your DBA to run the grant below."
        )
        st.code(f"GRANT SELECT_CATALOG_ROLE TO {user or '<your_user>'};", language="sql")
    elif "ORA-13609" in msg or "ORA-13600" in msg:
        st.error(
            "🧠 **SQL Tuning Advisor privileges missing.** The database user needs the "
            "ADVISOR privilege and EXECUTE on DBMS_SQLTUNE. "
            "This also requires an Oracle Tuning Pack license. "
            "Ask your DBA to run the grants below."
        )
        st.code(
            f"GRANT ADVISOR                  TO {user or '<your_user>'};\n"
            f"GRANT EXECUTE ON DBMS_SQLTUNE  TO {user or '<your_user>'};",
            language="sql",
        )
    else:
        st.error(f"⚠️ **Unexpected database error:** {msg[:300]}")
        with st.expander("🔍 Full error details"):
            st.exception(exc)


# ── Queries ───────────────────────────────────────────────────────────────────

SQL_SUMMARY_QUERY = """
SELECT
    SQL_ID,
    PARSING_SCHEMA_NAME,
    NVL(MODULE,'(unknown)')             AS MODULE,
    NVL(ACTION,'(unknown)')             AS ACTION,
    MIN(FIRST_LOAD_TIME)                AS FIRST_LOAD_TIME,
    MAX(LAST_ACTIVE_TIME)               AS LAST_ACTIVE_TIME,
    SUM(EXECUTIONS)                     AS TOTAL_EXECUTIONS,
    COUNT(DISTINCT CASE
        WHEN PLAN_HASH_VALUE != 0
         AND EXECUTIONS      >  0
        THEN PLAN_HASH_VALUE
    END)                                AS PLAN_COUNT
FROM V$SQL
WHERE (:sql_id_filter IS NULL OR SQL_ID = :sql_id_filter)
  AND (:schema_filter IS NULL OR UPPER(PARSING_SCHEMA_NAME) = UPPER(:schema_filter))
  AND (:module_filter IS NULL OR UPPER(NVL(MODULE,'(unknown)')) = UPPER(:module_filter))
GROUP BY SQL_ID, PARSING_SCHEMA_NAME, MODULE, ACTION
HAVING COUNT(DISTINCT CASE
        WHEN PLAN_HASH_VALUE != 0
         AND EXECUTIONS      >  0
        THEN PLAN_HASH_VALUE
    END) >= 1
ORDER BY PLAN_COUNT DESC, LAST_ACTIVE_TIME DESC NULLS LAST
"""

MODULE_SUMMARY_QUERY = """
SELECT
    NVL(MODULE,'(unknown)')     AS MODULE,
    COUNT(DISTINCT SQL_ID)      AS TOTAL_SQLS,
    SUM(CASE WHEN plan_cnt > 1 THEN 1 ELSE 0 END) AS FLUCTUATING_SQLS,
    SUM(EXECUTIONS)             AS TOTAL_EXECUTIONS,
    ROUND(SUM(ELAPSED_TIME) / NULLIF(SUM(EXECUTIONS),0) / 1e6, 4) AS AVG_ELAPSED_SEC
FROM (
    SELECT MODULE, SQL_ID,
           COUNT(DISTINCT CASE
               WHEN PLAN_HASH_VALUE != 0
                AND EXECUTIONS      >  0
               THEN PLAN_HASH_VALUE
           END)                 AS plan_cnt,
           SUM(EXECUTIONS)      AS EXECUTIONS,
           SUM(ELAPSED_TIME)    AS ELAPSED_TIME
    FROM V$SQL GROUP BY MODULE, SQL_ID
)
GROUP BY MODULE
ORDER BY FLUCTUATING_SQLS DESC, TOTAL_SQLS DESC
"""

SQL_PLANS_AVG_QUERY = """
SELECT
    PLAN_HASH_VALUE,
    COUNT(*)                                                        AS CHILD_CURSORS,
    SUM(EXECUTIONS)                                                 AS TOTAL_EXECUTIONS,
    ROUND(SUM(ELAPSED_TIME)  / NULLIF(SUM(EXECUTIONS),0) / 1e6, 4) AS AVG_ELAPSED_SEC,
    ROUND(SUM(CPU_TIME)      / NULLIF(SUM(EXECUTIONS),0) / 1e6, 4) AS AVG_CPU_SEC,
    ROUND(SUM(BUFFER_GETS)   / NULLIF(SUM(EXECUTIONS),0), 0)       AS AVG_BUFFER_GETS,
    ROUND(SUM(DISK_READS)    / NULLIF(SUM(EXECUTIONS),0), 0)       AS AVG_DISK_READS,
    ROUND(SUM(ROWS_PROCESSED)/ NULLIF(SUM(EXECUTIONS),0), 0)       AS AVG_ROWS,
    MAX(LAST_ACTIVE_TIME)                                           AS LAST_ACTIVE_TIME
FROM V$SQL
WHERE SQL_ID          = :sql_id
  AND PLAN_HASH_VALUE != 0
  AND EXECUTIONS      >  0
GROUP BY PLAN_HASH_VALUE
ORDER BY AVG_ELAPSED_SEC ASC NULLS LAST
"""

SQL_PLAN_STEPS_QUERY = """
SELECT ID, PARENT_ID, OPERATION, OPTIONS,
       OBJECT_OWNER, OBJECT_NAME,
       ACCESS_PREDICATES, FILTER_PREDICATES,
       COST, CARDINALITY, BYTES
FROM V$SQL_PLAN
WHERE SQL_ID = :sql_id AND PLAN_HASH_VALUE = :plan_hash_value
ORDER BY ID
"""

SQL_TEXT_QUERY = "SELECT SQL_FULLTEXT FROM V$SQLAREA WHERE SQL_ID = :sql_id"

# ── AWR / History queries ──────────────────────────────────────────────────────

AWR_SNAPSHOTS_QUERY = """
SELECT
    SNAP_ID,
    TO_CHAR(BEGIN_INTERVAL_TIME, 'YYYY-MM-DD HH24:MI') AS BEGIN_TIME,
    TO_CHAR(END_INTERVAL_TIME,   'YYYY-MM-DD HH24:MI') AS END_TIME,
    ROUND((CAST(END_INTERVAL_TIME AS DATE)
           - CAST(BEGIN_INTERVAL_TIME AS DATE)) * 24 * 60, 1) AS DURATION_MIN
FROM DBA_HIST_SNAPSHOT
WHERE DBID = (SELECT DBID FROM V$DATABASE)
ORDER BY SNAP_ID DESC
FETCH FIRST :max_rows ROWS ONLY
"""

AWR_TOP_SQL_QUERY = """
SELECT
    s.SQL_ID,
    s.PLAN_HASH_VALUE,
    COALESCE(t.SQL_TEXT, '(not available)')          AS SQL_TEXT_SHORT,
    SUM(s.EXECUTIONS_DELTA)                           AS TOTAL_EXECUTIONS,
    ROUND(SUM(s.ELAPSED_TIME_DELTA)   / 1e6, 2)      AS TOTAL_ELAPSED_SEC,
    ROUND(SUM(s.CPU_TIME_DELTA)       / 1e6, 2)      AS TOTAL_CPU_SEC,
    SUM(s.BUFFER_GETS_DELTA)                          AS TOTAL_BUFFER_GETS,
    SUM(s.DISK_READS_DELTA)                           AS TOTAL_DISK_READS,
    ROUND(SUM(s.ELAPSED_TIME_DELTA)
          / NULLIF(SUM(s.EXECUTIONS_DELTA),0) / 1e6, 4) AS AVG_ELAPSED_SEC,
    COUNT(DISTINCT CASE
        WHEN s.PLAN_HASH_VALUE  != 0
         AND s.EXECUTIONS_DELTA >  0
        THEN s.PLAN_HASH_VALUE
    END)                                              AS PLAN_COUNT
FROM DBA_HIST_SQLSTAT s
LEFT JOIN (
    SELECT SQL_ID, DBMS_LOB.SUBSTR(SQL_TEXT, 120, 1) AS SQL_TEXT
    FROM DBA_HIST_SQLTEXT
) t ON t.SQL_ID = s.SQL_ID
WHERE s.DBID             = (SELECT DBID FROM V$DATABASE)
  AND s.SNAP_ID         >= :begin_snap_id
  AND s.SNAP_ID         <= :end_snap_id
  AND s.EXECUTIONS_DELTA >  0
  AND s.PLAN_HASH_VALUE  != 0
GROUP BY s.SQL_ID, s.PLAN_HASH_VALUE, t.SQL_TEXT
ORDER BY TOTAL_ELAPSED_SEC DESC NULLS LAST
FETCH FIRST 50 ROWS ONLY
"""

SQL_HISTORY_QUERY = """
SELECT
    s.SNAP_ID,
    TO_CHAR(sn.BEGIN_INTERVAL_TIME, 'YYYY-MM-DD HH24:MI') AS SNAP_TIME,
    s.PLAN_HASH_VALUE,
    s.EXECUTIONS_DELTA                                          AS EXECUTIONS,
    ROUND(s.ELAPSED_TIME_DELTA  / NULLIF(s.EXECUTIONS_DELTA,0) / 1e6, 4) AS AVG_ELAPSED_SEC,
    ROUND(s.CPU_TIME_DELTA      / NULLIF(s.EXECUTIONS_DELTA,0) / 1e6, 4) AS AVG_CPU_SEC,
    ROUND(s.BUFFER_GETS_DELTA   / NULLIF(s.EXECUTIONS_DELTA,0), 0)       AS AVG_BUFFER_GETS,
    ROUND(s.DISK_READS_DELTA    / NULLIF(s.EXECUTIONS_DELTA,0), 0)       AS AVG_DISK_READS,
    ROUND(s.ROWS_PROCESSED_DELTA/ NULLIF(s.EXECUTIONS_DELTA,0), 0)       AS AVG_ROWS,
    ROUND(s.ELAPSED_TIME_DELTA  / 1e6, 2)                                AS TOTAL_ELAPSED_SEC,
    s.OPTIMIZER_COST
FROM DBA_HIST_SQLSTAT s
JOIN DBA_HIST_SNAPSHOT sn
     ON s.SNAP_ID = sn.SNAP_ID
    AND s.DBID    = sn.DBID
    AND s.INSTANCE_NUMBER = sn.INSTANCE_NUMBER
WHERE s.SQL_ID = :sql_id
  AND s.DBID   = (SELECT DBID FROM V$DATABASE)
  AND s.EXECUTIONS_DELTA > 0
  AND s.SNAP_ID >= :begin_snap_id
  AND s.SNAP_ID <= :end_snap_id
ORDER BY s.SNAP_ID
"""

SQL_HIST_PLAN_STEPS_QUERY = """
SELECT
    ID, PARENT_ID, OPERATION, OPTIONS,
    OBJECT_OWNER, OBJECT_NAME,
    ACCESS_PREDICATES, FILTER_PREDICATES,
    COST, CARDINALITY, BYTES
FROM DBA_HIST_SQL_PLAN
WHERE SQL_ID          = :sql_id
  AND PLAN_HASH_VALUE = :plan_hash_value
  AND DBID            = (SELECT DBID FROM V$DATABASE)
ORDER BY ID
"""

AWR_WAIT_EVENTS_QUERY = """
SELECT
    e.EVENT_NAME,
    SUM(e.TOTAL_WAITS_FG)                              AS TOTAL_WAITS,
    ROUND(SUM(e.TIME_WAITED_MICRO_FG) / 1e6, 2)       AS TIME_WAITED_SEC,
    ROUND(SUM(e.TIME_WAITED_MICRO_FG)
          / NULLIF(SUM(e.TOTAL_WAITS_FG),0) / 1000, 4) AS AVG_WAIT_MS,
    e.WAIT_CLASS
FROM DBA_HIST_SYSTEM_EVENT e
JOIN DBA_HIST_SNAPSHOT sn ON e.SNAP_ID = sn.SNAP_ID AND e.DBID = sn.DBID
WHERE e.DBID     = (SELECT DBID FROM V$DATABASE)
  AND e.SNAP_ID >= :begin_snap_id
  AND e.SNAP_ID <= :end_snap_id
  AND e.WAIT_CLASS != 'Idle'
  AND e.TOTAL_WAITS_FG > 0
GROUP BY e.EVENT_NAME, e.WAIT_CLASS
ORDER BY TIME_WAITED_SEC DESC NULLS LAST
FETCH FIRST 20 ROWS ONLY
"""

AWR_LOAD_PROFILE_QUERY = """
SELECT
    STAT_NAME,
    ROUND(SUM(VALUE) / NULLIF(
        (SELECT SUM(ELAPSED_TIME) / 1e6
         FROM DBA_HIST_SNAPSHOT
         WHERE DBID = (SELECT DBID FROM V$DATABASE)
           AND SNAP_ID >= :begin_snap_id
           AND SNAP_ID <= :end_snap_id), 0), 4) AS VALUE_PER_SEC
FROM DBA_HIST_SYSSTAT s
WHERE s.DBID    = (SELECT DBID FROM V$DATABASE)
  AND s.SNAP_ID >= :begin_snap_id
  AND s.SNAP_ID <= :end_snap_id
  AND s.STAT_NAME IN (
      'user calls', 'execute count', 'parse count (total)',
      'parse count (hard)', 'physical reads', 'physical writes',
      'redo size', 'session logical reads', 'user commits', 'user rollbacks'
  )
GROUP BY STAT_NAME
ORDER BY STAT_NAME
"""


# ── AWR helper functions ───────────────────────────────────────────────────────

def fetch_awr_snapshots(cfg: dict, max_rows: int = 200) -> pd.DataFrame:
    """Return list of recent AWR snapshots for a given DB config."""
    try:
        df = query_one_db(cfg, AWR_SNAPSHOTS_QUERY, {"max_rows": max_rows})
        df.drop(columns=["DATABASE"], errors="ignore", inplace=True)
        for col in ("SNAP_ID", "DURATION_MIN"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as exc:
        return pd.DataFrame({"error": [str(exc)]})


def generate_awr_report_text(cfg: dict, begin_snap: int, end_snap: int) -> str:
    """Call DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_TEXT and return joined lines."""
    sql = """
        SELECT OUTPUT
        FROM TABLE(
            DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_TEXT(
                (SELECT DBID            FROM V$DATABASE),
                (SELECT INSTANCE_NUMBER FROM V$INSTANCE),
                :b, :e, 0
            )
        )
    """
    try:
        with get_connection(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"b": begin_snap, "e": end_snap})
                rows = cur.fetchall()
        return "\n".join(r[0] or "" for r in rows)
    except Exception as exc:
        return f"ERROR: {exc}"


def generate_awr_report_html(cfg: dict, begin_snap: int, end_snap: int) -> str:
    """Call DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML and return joined HTML lines."""
    sql = """
        SELECT OUTPUT
        FROM TABLE(
            DBMS_WORKLOAD_REPOSITORY.AWR_REPORT_HTML(
                (SELECT DBID            FROM V$DATABASE),
                (SELECT INSTANCE_NUMBER FROM V$INSTANCE),
                :b, :e, 0
            )
        )
    """
    try:
        with get_connection(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"b": begin_snap, "e": end_snap})
                rows = cur.fetchall()
        html = "\n".join(r[0] or "" for r in rows)
        # Inject a small override to ensure the report is readable inside the dashboard
        inject = (
            "<style>"
            "body{font-family:Arial,sans-serif;font-size:13px;}"
            "table{border-collapse:collapse;width:100%;}"
            "td,th{padding:4px 8px;border:1px solid #ccc;}"
            "th{background:#2e55c8;color:#fff;}"
            "tr:nth-child(even){background:#f5f8ff;}"
            "a{color:#2e55c8;}"
            "</style>"
        )
        # Insert after <head> tag if present, otherwise prepend
        if "<head>" in html.lower():
            html = html.replace("<head>", f"<head>{inject}", 1)
        else:
            html = inject + html
        return html
    except Exception as exc:
        return f"ERROR: {exc}"


def _numeric_cols_to_float(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce object columns that are fully numeric to float.
    Skips columns where coercion would introduce new NaN (i.e. text columns)."""
    for col in df.columns:
        if df[col].dtype == object:
            converted = pd.to_numeric(df[col], errors="coerce")
            # Only replace if no new NaN values were introduced
            if converted.isna().sum() == df[col].isna().sum():
                df[col] = converted
    return df


# ── AWR Performance Analysis Engine ───────────────────────────────────────────

def analyze_awr_performance(
    wait_df: pd.DataFrame | None,
    top_sql_df: pd.DataFrame | None,
) -> dict:
    """
    Rule-based interpretation of AWR data.
    Returns health_score (0-100), findings list, and summary text.
    Each finding has: severity (critical/warning/info), title, detail, recommendation.
    """
    findings: list[dict] = []
    score = 100

    # ── Wait event rules ──────────────────────────────────────────────────────
    if wait_df is not None and not wait_df.empty:
        total_wait = wait_df["TIME_WAITED_SEC"].sum()
        wc_agg = (
            wait_df.groupby("WAIT_CLASS")["TIME_WAITED_SEC"]
            .sum()
            .sort_values(ascending=False)
        )

        # User I/O dominance
        user_io = wc_agg.get("User I/O", 0)
        if total_wait > 0 and user_io / total_wait > 0.40:
            score -= 20
            findings.append({
                "severity": "critical",
                "title": "High User I/O Wait",
                "detail": (
                    f"User I/O accounts for {user_io / total_wait * 100:.1f}% "
                    f"of total wait time ({user_io:,.1f}s). "
                    "Excessive physical reads/writes are slowing the database."
                ),
                "recommendation": (
                    "Review top SQLs for full table scans and missing indexes. "
                    "Check buffer cache hit ratio (target >95%). "
                    "Consider increasing DB_CACHE_SIZE or adding SSD-backed storage."
                ),
            })

        # Concurrency issues
        concurrency = wc_agg.get("Concurrency", 0)
        if total_wait > 0 and concurrency / total_wait > 0.15:
            score -= 15
            findings.append({
                "severity": "critical",
                "title": "High Concurrency Wait",
                "detail": (
                    f"Concurrency waits are {concurrency / total_wait * 100:.1f}% "
                    "of total wait time — multiple sessions competing for internal resources."
                ),
                "recommendation": (
                    "Investigate latch contention and buffer busy waits. "
                    "Ensure application uses bind variables (reduces hard parsing). "
                    "Review SHARED_POOL_SIZE and DB_CACHE_SIZE."
                ),
            })

        # Per-event checks
        for _, row in wait_df.iterrows():
            evt      = str(row["EVENT_NAME"]).lower()
            waited   = float(row.get("TIME_WAITED_SEC", 0) or 0)
            avg_ms   = float(row.get("AVG_WAIT_MS", 0) or 0)
            waits    = int(row.get("TOTAL_WAITS", 0) or 0)

            if "db file sequential read" in evt and waited > 60:
                score -= 15
                findings.append({
                    "severity": "warning",
                    "title": "Slow Single-Block I/O (db file sequential read)",
                    "detail": (
                        f"Total wait: {waited:,.1f}s, avg {avg_ms:.2f}ms per read "
                        f"({waits:,} waits). Typically caused by index lookups hitting "
                        "physical disk instead of buffer cache."
                    ),
                    "recommendation": (
                        "Increase DB_CACHE_SIZE so hot index blocks stay in memory. "
                        "Check top SQLs for selective queries on large tables — "
                        "ensure statistics are up to date."
                    ),
                })

            elif "db file scattered read" in evt and waited > 30:
                score -= 10
                findings.append({
                    "severity": "warning",
                    "title": "Excessive Full Table Scans (db file scattered read)",
                    "detail": (
                        f"Multi-block I/O wait: {waited:,.1f}s total ({waits:,} waits). "
                        "Usually caused by full table scans on large tables."
                    ),
                    "recommendation": (
                        "Identify SQLs performing full table scans in V$SQL_PLAN. "
                        "Add appropriate indexes or rewrite WHERE clauses. "
                        "If intentional (batch jobs), consider parallel query."
                    ),
                })

            elif "log file sync" in evt and waited > 20:
                score -= 12
                findings.append({
                    "severity": "warning",
                    "title": "High COMMIT Latency (log file sync)",
                    "detail": (
                        f"COMMIT is slow: {waited:,.1f}s total, avg {avg_ms:.2f}ms per commit. "
                        "This blocks transactions from completing and reduces throughput."
                    ),
                    "recommendation": (
                        "Move redo logs to faster storage (SSD/NVMe). "
                        "Reduce commit frequency by batching DML. "
                        "Check LGWR I/O performance and redo log placement."
                    ),
                })

            elif "log file parallel write" in evt and waited > 20:
                score -= 10
                findings.append({
                    "severity": "warning",
                    "title": "Redo Log I/O Bottleneck (log file parallel write)",
                    "detail": (
                        f"LGWR is slow writing to redo logs: {waited:,.1f}s total, "
                        f"avg {avg_ms:.2f}ms. Impacting all committing transactions."
                    ),
                    "recommendation": (
                        "Move redo log files to dedicated, fast disks (SSD). "
                        "Avoid placing redo logs on the same disk as data files. "
                        "Consider increasing LOG_BUFFER size."
                    ),
                })

            elif "enq: tx" in evt and waited > 10:
                score -= 18
                findings.append({
                    "severity": "critical",
                    "title": "Row Lock Contention (TX Enqueue)",
                    "detail": (
                        f"Transactions are blocking each other: {waited:,.1f}s total. "
                        "Long-running DML or uncommitted transactions are causing waits."
                    ),
                    "recommendation": (
                        "Identify blocking sessions with V$SESSION / GV$LOCK. "
                        "Shorten transaction duration. Ensure COMMIT/ROLLBACK after DML. "
                        "Check for missing COMMIT in application code."
                    ),
                })

            elif "enq: hw" in evt and waited > 5:
                score -= 8
                findings.append({
                    "severity": "warning",
                    "title": "High-Water Mark Contention (enq: HW)",
                    "detail": (
                        f"Segment HWM contention: {waited:,.1f}s. "
                        "Multiple sessions extending the same segment simultaneously."
                    ),
                    "recommendation": (
                        "Pre-allocate space for heavily-inserted tables (ALLOCATE EXTENT). "
                        "Use ASSM (Automatic Segment Space Management). "
                        "Consider partitioning large tables."
                    ),
                })

            elif "latch" in evt and "shared pool" in evt and waited > 5:
                score -= 12
                findings.append({
                    "severity": "warning",
                    "title": "Shared Pool Latch Contention",
                    "detail": (
                        f"High shared pool latch waits: {waited:,.1f}s. "
                        "Usually caused by excessive hard parsing or shared pool fragmentation."
                    ),
                    "recommendation": (
                        "Use bind variables in all SQL (most impactful fix). "
                        "Set CURSOR_SHARING=FORCE as a temporary measure. "
                        "Increase SHARED_POOL_SIZE. Pin frequently-used packages."
                    ),
                })

            elif "buffer busy" in evt and waited > 10:
                score -= 10
                findings.append({
                    "severity": "warning",
                    "title": "Hot Block Contention (buffer busy waits)",
                    "detail": (
                        f"Multiple sessions competing for the same data blocks: {waited:,.1f}s. "
                        "Common with sequence-based inserts or small, frequently-accessed segments."
                    ),
                    "recommendation": (
                        "Use reverse key indexes for sequence-based primary keys. "
                        "Increase FREELIST GROUPS for MSSM tablespaces (or switch to ASSM). "
                        "Consider hash partitioning on hot tables."
                    ),
                })

            elif "library cache" in evt and waited > 5:
                score -= 10
                findings.append({
                    "severity": "warning",
                    "title": "Library Cache Contention",
                    "detail": (
                        f"Library cache lock/pin waits: {waited:,.1f}s. "
                        "Often caused by DDL during peak hours or cursor invalidation."
                    ),
                    "recommendation": (
                        "Avoid DDL (ALTER TABLE, GRANTS) during peak hours. "
                        "Use bind variables. Increase SHARED_POOL_SIZE. "
                        "Check for excessive cursor invalidations."
                    ),
                })

            elif "gc " in evt and waited > 30:
                score -= 12
                findings.append({
                    "severity": "critical",
                    "title": "RAC Interconnect / Global Cache Contention",
                    "detail": (
                        f"Global Cache (RAC) waits: {waited:,.1f}s ({waits:,} waits). "
                        "Nodes are competing heavily for the same data blocks across interconnect."
                    ),
                    "recommendation": (
                        "Review data/application partitioning across RAC nodes. "
                        "Check interconnect bandwidth and latency. "
                        "Use services to pin workloads to specific nodes (affinity)."
                    ),
                })

    # ── Top SQL rules ─────────────────────────────────────────────────────────
    if top_sql_df is not None and not top_sql_df.empty:
        sql_agg = (
            top_sql_df.groupby("SQL_ID", as_index=False)
            .agg(
                TOTAL_ELAPSED_SEC =("TOTAL_ELAPSED_SEC",  "sum"),
                TOTAL_EXECUTIONS  =("TOTAL_EXECUTIONS",   "sum"),
                TOTAL_DISK_READS  =("TOTAL_DISK_READS",   "sum"),
                TOTAL_BUFFER_GETS =("TOTAL_BUFFER_GETS",  "sum"),
                MAX_PLAN_COUNT    =("PLAN_COUNT",          "max"),
                AVG_ELAPSED_SEC   =("AVG_ELAPSED_SEC",    "max"),
            )
        )

        # Plan churn
        churning = sql_agg[sql_agg["MAX_PLAN_COUNT"] > 1].sort_values(
            "TOTAL_ELAPSED_SEC", ascending=False
        )
        if not churning.empty:
            score -= min(20, len(churning) * 4)
            sql_list = ", ".join(churning["SQL_ID"].head(5).tolist())
            findings.append({
                "severity": "critical" if len(churning) > 3 else "warning",
                "title": f"SQL Plan Instability — {len(churning)} SQL(s) Changed Plans",
                "detail": (
                    f"{len(churning)} SQL(s) used multiple execution plans during this period: "
                    f"{sql_list}. Plan changes often cause sudden performance degradation."
                ),
                "recommendation": (
                    "Lock good plans using SQL Plan Baselines (DBMS_SPM). "
                    "Run SQL Tuning Advisor on these SQLs. "
                    "Ensure optimizer statistics are fresh (DBMS_STATS). "
                    "Check for schema or parameter changes that invalidated plans."
                ),
            })

        # High avg elapsed per execution
        slow = sql_agg[sql_agg["AVG_ELAPSED_SEC"] > 5].sort_values(
            "AVG_ELAPSED_SEC", ascending=False
        ).head(3)
        for _, row in slow.iterrows():
            score -= 5
            findings.append({
                "severity": "warning",
                "title": f"Slow SQL: {row['SQL_ID']} (avg {row['AVG_ELAPSED_SEC']:.2f}s/exec)",
                "detail": (
                    f"SQL_ID {row['SQL_ID']} averages {row['AVG_ELAPSED_SEC']:.2f}s per execution "
                    f"over {int(row['TOTAL_EXECUTIONS']):,} executions "
                    f"({row['TOTAL_ELAPSED_SEC']:,.1f}s total DB time)."
                ),
                "recommendation": (
                    "Run SQL Tuning Advisor on this SQL. "
                    "Review its execution plan for full scans or Cartesian joins. "
                    "Check bind variable peeking and histogram statistics."
                ),
            })

        # High physical I/O
        high_disk = sql_agg[sql_agg["TOTAL_DISK_READS"] > 500_000].sort_values(
            "TOTAL_DISK_READS", ascending=False
        ).head(3)
        for _, row in high_disk.iterrows():
            score -= 5
            findings.append({
                "severity": "warning",
                "title": f"High Physical Reads: {row['SQL_ID']} ({int(row['TOTAL_DISK_READS']):,} reads)",
                "detail": (
                    f"SQL_ID {row['SQL_ID']} performed {int(row['TOTAL_DISK_READS']):,} physical disk reads. "
                    "High disk reads suggest data is not cached in the buffer pool."
                ),
                "recommendation": (
                    "Add indexes to avoid full table scans. "
                    "Increase buffer cache to keep hot data in memory. "
                    "Review SQL WHERE clauses for selectivity."
                ),
            })

    # ── No data case ──────────────────────────────────────────────────────────
    if wait_df is None and top_sql_df is None:
        findings.append({
            "severity": "info",
            "title": "No data loaded yet",
            "detail": "Click 'Analyze Top SQL' and 'Wait Events Analysis' first, then re-run.",
            "recommendation": "Load wait events and top SQL data before running the analysis.",
        })

    # ── If no issues found ────────────────────────────────────────────────────
    if not findings:
        findings.append({
            "severity": "info",
            "title": "No significant issues detected",
            "detail": "Wait events and SQL performance are within healthy thresholds.",
            "recommendation": (
                "Continue monitoring regularly. "
                "Consider reviewing optimizer statistics freshness and redo log sizing."
            ),
        })

    score = max(0, score)

    if score >= 85:
        status = "Healthy"
        status_color = "#4ade80"
        status_bg    = "#f0fdf4"
        summary = (
            "Your database is performing well. "
            "No critical bottlenecks were detected in this AWR window."
        )
    elif score >= 60:
        status = "Needs Attention"
        status_color = "#d97706"
        status_bg    = "#fffbeb"
        summary = (
            "Your database has some performance concerns. "
            "Review the findings below and address warnings before they become critical."
        )
    else:
        status = "Critical Issues"
        status_color = "#dc2626"
        status_bg    = "#fff1f0"
        summary = (
            "Your database is experiencing significant performance issues. "
            "Immediate investigation and remediation is recommended."
        )

    return {
        "health_score": score,
        "status":       status,
        "status_color": status_color,
        "status_bg":    status_bg,
        "summary":      summary,
        "findings":     findings,
    }


# ── Ask the Dashboard (context + optional LLM) ────────────────────────────────

def _df_to_context_chunk(name: str, df: pd.DataFrame, max_rows: int) -> str:
    if df is None or df.empty:
        return f"## {name}\n(empty)\n"
    sample = df.head(max_rows)
    text = sample.to_csv(index=False)
    note = "" if len(df) <= max_rows else f"\n… truncated; total rows: {len(df)}\n"
    return f"## {name}\n{text}{note}"


def build_dashboard_context(
    summary_df: pd.DataFrame,
    module_df: pd.DataFrame,
    active_cfgs: list[dict],
    sql_id_filter,
    schema_filter,
    module_filter,
) -> str:
    """Compact text-only snapshot of what is currently loaded (for Q&A)."""
    db_names = ", ".join(c["name"] for c in active_cfgs)
    fluctuating = summary_df[summary_df["PLAN_COUNT"] > 1]
    stable = summary_df[summary_df["PLAN_COUNT"] == 1]
    lines = [
        "# Oracle SQL Plan Dashboard — loaded snapshot",
        f"- Databases queried ({len(active_cfgs)}): {db_names}",
        f"- Filters: SQL_ID={sql_id_filter or '(none)'}, "
        f"Schema={schema_filter or '(none)'}, Module={module_filter or '(all)'}",
        f"- Total summary rows: {len(summary_df)}",
        f"- Fluctuating SQLs (PLAN_COUNT > 1): {len(fluctuating)}",
        f"- Stable SQLs (PLAN_COUNT == 1): {len(stable)}",
        f"- Max PLAN_COUNT in data: {int(summary_df['PLAN_COUNT'].max())}",
        "",
    ]
    db_agg = (
        summary_df.groupby("DATABASE", as_index=False)
        .agg(
            TOTAL_SQLS=("SQL_ID", "count"),
            FLUCTUATING_SQLS=("PLAN_COUNT", lambda x: int((x > 1).sum())),
            STABLE_SQLS=("PLAN_COUNT", lambda x: int((x == 1).sum())),
            TOTAL_EXECUTIONS=("TOTAL_EXECUTIONS", "sum"),
            MAX_PLANS=("PLAN_COUNT", "max"),
        )
        .sort_values("FLUCTUATING_SQLS", ascending=False)
    )
    lines.append("## Aggregated by DATABASE\n")
    lines.append(db_agg.to_csv(index=False))
    lines.append("")
    lines.append(_df_to_context_chunk("MODULE summary (aggregated)", module_df, ASK_CONTEXT_MOD_ROWS))
    lines.append(_df_to_context_chunk("SQL summary rows (sample)", summary_df, ASK_CONTEXT_SQL_ROWS))
    return "\n".join(lines)


def _answer_dashboard_heuristic(
    question: str,
    summary_df: pd.DataFrame,
    module_df: pd.DataFrame,
    active_cfgs: list[dict],
) -> str:
    """Deterministic answers from the same DataFrames the charts use (no external API)."""
    q_raw = question.strip()
    q = q_raw.lower()
    if not q:
        return "Please enter a question."

    fluctuating = summary_df[summary_df["PLAN_COUNT"] > 1]
    stable = summary_df[summary_df["PLAN_COUNT"] == 1]
    db_agg = (
        summary_df.groupby("DATABASE", as_index=False)
        .agg(
            TOTAL_SQLS=("SQL_ID", "count"),
            FLUCTUATING_SQLS=("PLAN_COUNT", lambda x: int((x > 1).sum())),
            STABLE_SQLS=("PLAN_COUNT", lambda x: int((x == 1).sum())),
            TOTAL_EXECUTIONS=("TOTAL_EXECUTIONS", "sum"),
            MAX_PLANS=("PLAN_COUNT", "max"),
        )
        .sort_values("FLUCTUATING_SQLS", ascending=False)
    )

    def fmt_top_sql(n: int = 10) -> str:
        top = summary_df.nlargest(n, "PLAN_COUNT")[
            ["DATABASE", "SQL_ID", "MODULE", "PLAN_COUNT", "TOTAL_EXECUTIONS"]
        ]
        return top.to_string(index=False)

    def quick_snapshot(extra: str = "") -> str:
        """Always-useful summary when no specific intent matched."""
        db_lines = db_agg[
            ["DATABASE", "FLUCTUATING_SQLS", "STABLE_SQLS", "TOTAL_SQLS", "MAX_PLANS"]
        ].head(25)
        parts = [
            "**Snapshot from the current dashboard load:**",
            "",
            f"- **Summary rows:** {len(summary_df)} (distinct SQL_ID / schema / module / action groups)",
            f"- **Fluctuating** (PLAN_COUNT > 1): **{len(fluctuating)}**",
            f"- **Stable** (single plan): **{len(stable)}**",
            f"- **Highest PLAN_COUNT:** {int(summary_df['PLAN_COUNT'].max())}",
            f"- **Databases:** {len(active_cfgs)} — {', '.join(c['name'] for c in active_cfgs)}",
            "",
            "**Per-database breakdown:**",
            "```",
            db_lines.to_string(index=False),
            "```",
        ]
        if not module_df.empty:
            parts.extend(
                [
                    "",
                    "**Modules with most fluctuating SQLs (top 10):**",
                    "```",
                    module_df.nlargest(10, "FLUCTUATING_SQLS")[
                        ["MODULE", "FLUCTUATING_SQLS", "TOTAL_SQLS"]
                    ].to_string(index=False),
                    "```",
                ]
            )
        if extra:
            parts.insert(0, extra.rstrip() + "\n\n---\n")
        parts.extend(
            [
                "",
                "*Tip:* Ask about **counts**, **worst database**, **top SQL_IDs**, **modules**, "
                "or paste a **13-character SQL_ID**. Set **OPENAI_API_KEY** for broader "
                "natural-language answers.",
            ]
        )
        return "\n".join(parts)

    # Oracle SQL_ID in the question (13 chars, alphanumeric)
    sid_m = re.search(r"\b([a-z0-9]{13})\b", q_raw, re.I)
    if sid_m:
        sid = sid_m.group(1).upper()
        sub = summary_df[summary_df["SQL_ID"].str.upper() == sid]
        if sub.empty:
            return (
                f"No summary row for **`{sid}`** in this load (it may be filtered out or "
                "not present in V$SQL for your filters). Showing overview:\n\n"
                + quick_snapshot()
            )
        return f"Rows for SQL_ID **`{sid}`**:\n\n```\n{sub.to_string(index=False)}\n```"

    # Keyword-style intents (order matters for overlaps)
    if any(k in q for k in ("how many database", "which database", "list database", "what database")):
        return (
            f"Databases in this load ({len(active_cfgs)}): "
            + ", ".join(c["name"] for c in active_cfgs)
        )

    if ("healthy" in q or "health" in q) and ("database" in q or "db" in q):
        ok = db_agg[db_agg["FLUCTUATING_SQLS"] == 0]["DATABASE"].tolist()
        bad = db_agg[db_agg["FLUCTUATING_SQLS"] > 0]["DATABASE"].tolist()
        return (
            f"Databases with **no** fluctuating SQLs ({len(ok)}): {', '.join(ok) or 'none'}\n\n"
            f"Databases **with** fluctuations ({len(bad)}): {', '.join(bad) or 'none'}"
        )

    if any(
        k in q
        for k in (
            "worst database",
            "most fluctuat",
            "which db has",
            "highest fluctuat",
            "problem database",
            "issue database",
        )
    ):
        row = db_agg.iloc[0]
        return (
            f"By fluctuating SQL count, **`{row['DATABASE']}`** has the most "
            f"({int(row['FLUCTUATING_SQLS'])} fluctuating SQLs, "
            f"{int(row['TOTAL_SQLS'])} total SQL summary rows)."
        )

    if any(
        k in q
        for k in (
            "how many fluctuat",
            "plan fluctuat",
            "unstable sql",
            "multiple plan",
            "more than one plan",
            "plan instability",
            "changing plan",
            "volatile",
        )
    ) or ("plan" in q and "change" in q):
        return (
            f"There are **{len(fluctuating)}** SQL summary rows with PLAN_COUNT > 1 "
            f"(plan fluctuation) out of **{len(summary_df)}** rows total."
        )

    if ("stable" in q and ("sql" in q or "plan" in q or "how many" in q)) or q.strip() == "stable":
        return (
            f"**{len(stable)}** summary rows have a single plan (PLAN_COUNT == 1)."
        )

    if any(k in q for k in ("total sql", "how many sql", "sql ids", "how many rows", "how many statement")):
        return (
            f"The summary table has **{len(summary_df)}** rows (SQL_ID / schema / module / action groups). "
            f"**{len(fluctuating)}** are fluctuating; **{len(stable)}** are stable."
        )

    if "module" in q and not module_df.empty:
        top_m = module_df.nlargest(15, "FLUCTUATING_SQLS")[
            ["MODULE", "TOTAL_SQLS", "FLUCTUATING_SQLS", "TOTAL_EXECUTIONS"]
        ]
        return "Top modules by fluctuating SQLs:\n\n" + top_m.to_string(index=False)

    if any(
        k in q
        for k in (
            "top sql",
            "most plan",
            "highest plan",
            "plan count",
            "most plans",
            "which sql",
            "worst sql",
        )
    ):
        return "Top SQL_IDs by PLAN_COUNT:\n\n" + fmt_top_sql(15)

    if any(k in q for k in ("execution", "elapsed", "slow", "performance", "timing")):
        if module_df.empty:
            return "No module aggregate data loaded."
        worst = module_df.nlargest(10, "AVG_ELAPSED_SEC")[
            ["MODULE", "AVG_ELAPSED_SEC", "TOTAL_EXECUTIONS", "FLUCTUATING_SQLS"]
        ]
        return (
            "Highest average elapsed (module rollup; seconds):\n\n"
            + worst.to_string(index=False)
        )

    if any(
        k in q
        for k in (
            "compare",
            "each database",
            "per database",
            "breakdown",
            "cross-db",
            "cross db",
            "by database",
        )
    ):
        view = db_agg[
            ["DATABASE", "FLUCTUATING_SQLS", "STABLE_SQLS", "TOTAL_SQLS", "TOTAL_EXECUTIONS", "MAX_PLANS"]
        ]
        return "**Per-database summary:**\n\n```\n" + view.to_string(index=False) + "\n```"

    if any(k in q for k in ("max plan", "maximum plan", "largest plan count")):
        mx = int(summary_df["PLAN_COUNT"].max())
        hit = summary_df[summary_df["PLAN_COUNT"] == mx].head(10)
        return (
            f"Largest **PLAN_COUNT** in this load: **{mx}**.\n\n"
            f"Example rows:\n```\n{hit[['DATABASE','SQL_ID','MODULE','PLAN_COUNT']].to_string(index=False)}\n```"
        )

    if any(
        k in q
        for k in (
            "overview",
            "summarize",
            "summary",
            "what is this",
            "what does",
            "explain",
            "statistics",
            "stats",
            "snapshot",
        )
    ) or q in ("hi", "hello", "hey", "help", "hi."):
        return quick_snapshot()

    # Default: still answer with data instead of a dead-end message
    return quick_snapshot(
        "Here is an automatic overview based on your question "
        "(no exact keyword match — showing key figures)."
    )


def _llm_system_prompt() -> str:
    return (
        "You are an assistant that helps users understand their Oracle SQL Plan "
        "Fluctuation Dashboard data. Answer ONLY using the CONTEXT provided "
        "(loaded summary and module aggregates). If the answer is not in the context, "
        "say so and suggest which filter or tab might help. "
        "Be concise and use bullet lists when helpful."
    )


def ask_dashboard_llm(question: str, context: str):
    """
    OpenAI Chat Completions call. Returns (answer, error_or_none).
    Uses OPENAI_API_KEY; optional OPENAI_MODEL (default gpt-4o-mini).
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return "", None

    model    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    user_msg = f"CONTEXT:\n\n{context}\n\nUSER QUESTION:\n{question}"
    payload  = {
        "model": model,
        "messages": [
            {"role": "system", "content": _llm_system_prompt()},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens":  1200,
    }
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=ASK_HTTP_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"].strip(), None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(exc)
        return "", f"API HTTP error {exc.code}: {detail}"
    except Exception as exc:
        return "", str(exc)[:500]


def ask_ollama(question: str, context: str):
    """
    Local Ollama call via /api/chat (OpenAI-compatible endpoint).
    Returns (answer, error_or_none).
    """
    user_msg = f"CONTEXT:\n\n{context}\n\nUSER QUESTION:\n{question}"
    payload  = {
        "model":    OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _llm_system_prompt()},
            {"role": "user",   "content": user_msg},
        ],
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["message"]["content"].strip(), None
    except urllib.error.URLError as exc:
        return "", (
            f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
            "Make sure Ollama is running: open a terminal and run  ollama serve"
            f"  (detail: {exc.reason})"
        )
    except Exception as exc:
        return "", str(exc)[:500]


def ollama_is_running() -> bool:
    """Quick ping to check if Ollama server is up."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=2):
            return True
    except Exception:
        return False


def ollama_model_available() -> bool:
    """Check if OLLAMA_MODEL is already pulled."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "").split(":")[0] for m in body.get("models", [])]
        return OLLAMA_MODEL in names or any(OLLAMA_MODEL in n for n in names)
    except Exception:
        return False


# ── Advisor report renderer ───────────────────────────────────────────────────
_FINDING_META = {
    "SQL PROFILE":   ("🏷️", "#4f46e5", "#eef2ff"),
    "INDEX":         ("📑", "#d97706", "#fffbeb"),
    "STATISTICS":    ("📊", "#7c3aed", "#faf5ff"),
    "RESTRUCTURE":   ("🔧", "#dc2626", "#fff1f0"),
    "ALTERNATIVE":   ("🔀", "#059669", "#f0fdf4"),
    "MISCELLANEOUS": ("ℹ️", "#2563eb", "#eff6ff"),
}

def _finding_meta(text: str):
    upper = text.upper()
    for key, meta in _FINDING_META.items():
        if key in upper:
            return meta
    return ("💡", "#1e2a3a", "#f5f8ff")


def _render_advisor_report(report: str, sql_id: str) -> None:
    lines = report.splitlines()
    gen_lines, finding_blocks, plan_lines, error_lines = [], [], [], []
    current_section, current_finding = None, None

    for line in lines:
        up = line.upper().strip()
        if "GENERAL INFORMATION SECTION" in up:
            current_section = "general"
        elif "FINDINGS SECTION" in up:
            current_section = "findings"
        elif "EXPLAIN PLANS SECTION" in up:
            current_section = "plans"
        elif "ERRORS SECTION" in up:
            current_section = "errors"
        elif current_section == "general":
            gen_lines.append(line)
        elif current_section == "findings":
            m = re.match(r"^\s*-+\s*Finding\s+(\d+)\s*\((.+?)\)\s*-+", line, re.IGNORECASE)
            if m:
                current_finding = {"num": m.group(1), "type": m.group(2).strip(), "lines": []}
                finding_blocks.append(current_finding)
            elif current_finding is not None:
                current_finding["lines"].append(line)
        elif current_section == "plans":
            plan_lines.append(line)
        elif current_section == "errors":
            error_lines.append(line)

    total   = len(finding_blocks)
    has_sql = any("SQL PROFILE" in f["type"].upper() for f in finding_blocks)
    has_idx = any("INDEX"       in f["type"].upper() for f in finding_blocks)
    has_sta = any("STATISTIC"   in f["type"].upper() for f in finding_blocks)

    badge_html = f"""
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center;">
        <span style="font-size:1rem;font-weight:700;color:#1e2a3a;">SQL: <span style="color:#2e55c8;">{sql_id}</span></span>
        <span style="background:#eff6ff;color:#2563eb;border:1px solid #93c5fd;padding:3px 12px;border-radius:20px;font-size:0.8rem;font-weight:600;">{total} Finding{"s" if total!=1 else ""}</span>
        {"<span style='background:#eef2ff;color:#4f46e5;border:1px solid #a5b4fc;padding:3px 12px;border-radius:20px;font-size:0.8rem;font-weight:600;'>🏷️ SQL Profile</span>" if has_sql else ""}
        {"<span style='background:#fffbeb;color:#d97706;border:1px solid #fcd34d;padding:3px 12px;border-radius:20px;font-size:0.8rem;font-weight:600;'>📑 Index</span>" if has_idx else ""}
        {"<span style='background:#faf5ff;color:#7c3aed;border:1px solid #c4b5fd;padding:3px 12px;border-radius:20px;font-size:0.8rem;font-weight:600;'>📊 Statistics</span>" if has_sta else ""}
    </div>"""
    st.markdown(badge_html, unsafe_allow_html=True)

    if total == 0:
        st.info("No findings — current plan appears optimal.")
        with st.expander("📄 Raw Report"):
            st.text(report)
        return

    gen_text = "\n".join(gen_lines).strip()
    if gen_text:
        with st.expander("ℹ️ General Information", expanded=False):
            kv = re.findall(r"([A-Za-z][A-Za-z0-9 _/]+?)\s*:\s*(.+)", gen_text)
            if kv:
                st.dataframe(pd.DataFrame(kv, columns=["Property", "Value"]),
                             use_container_width=True, hide_index=True)
            else:
                st.text(gen_text)

    st.markdown("<div style='font-size:0.95rem;font-weight:700;color:#1e2a3a;margin:16px 0 10px 0;'>Recommendations</div>", unsafe_allow_html=True)

    for finding in finding_blocks:
        icon, accent, bg = _finding_meta(finding["type"])
        body = "\n".join(finding["lines"]).strip()
        rec  = re.search(r"Recommendation\s*\d*\s*\n(.*?)(?=Rationale|Action|$)", body, re.DOTALL | re.IGNORECASE)
        rat  = re.search(r"Rationale\s*\n(.*?)(?=Recommendation|Action|$)",         body, re.DOTALL | re.IGNORECASE)
        act  = re.search(r"Action\s*\d*\s*\n(.*?)(?=Recommendation|Rationale|$)",   body, re.DOTALL | re.IGNORECASE)
        rec_t = rec.group(1).strip() if rec else ""
        rat_t = rat.group(1).strip() if rat else ""
        act_t = act.group(1).strip() if act else ""
        if not rec_t and not rat_t and not act_t:
            rec_t = body

        card = f"""<div style="background:{bg};border:1px solid {accent};border-left:4px solid {accent};border-radius:10px;padding:16px 20px;margin-bottom:12px;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                <span style="font-size:1.2rem;">{icon}</span>
                <span style="font-size:0.95rem;font-weight:700;color:{accent};">Finding {finding["num"]} — {finding["type"]}</span>
            </div>"""
        if rec_t:
            card += f"""<div style="margin-bottom:8px;"><span style="font-size:0.75rem;font-weight:700;color:#5a7ab0;text-transform:uppercase;">Recommendation</span><div style="color:#1e2a3a;font-size:0.87rem;margin-top:4px;line-height:1.6;">{rec_t.replace(chr(10),"<br>")}</div></div>"""
        if rat_t:
            card += f"""<div style="margin-bottom:8px;"><span style="font-size:0.75rem;font-weight:700;color:#5a7ab0;text-transform:uppercase;">Rationale</span><div style="color:#3a4f6e;font-size:0.85rem;margin-top:4px;font-style:italic;line-height:1.6;">{rat_t.replace(chr(10),"<br>")}</div></div>"""
        if act_t:
            card += f"""<div><span style="font-size:0.75rem;font-weight:700;color:#5a7ab0;text-transform:uppercase;">Action</span><div style="color:#1e3a6e;font-size:0.85rem;margin-top:4px;font-family:monospace;background:#eef4ff;padding:8px 10px;border-radius:6px;line-height:1.6;">{act_t.replace(chr(10),"<br>")}</div></div>"""
        card += "</div>"
        st.markdown(card, unsafe_allow_html=True)

    plan_text = "\n".join(plan_lines).strip()
    if plan_text:
        with st.expander("📐 Explain Plans (Before / After)", expanded=False):
            st.code(plan_text, language="sql")
    if has_sql:
        with st.expander("✅ Accept the SQL Profile (run as DBA)"):
            st.code("EXEC DBMS_SQLTUNE.ACCEPT_SQL_PROFILE(\n  task_name => '<task_name>',\n  replace => TRUE,\n  force_match => TRUE\n);", language="sql")
    err_text = "\n".join(error_lines).strip()
    if err_text:
        with st.expander("⚠️ Advisor Errors"):
            st.warning(err_text)
    with st.expander("📄 Full Raw Report"):
        st.text(report)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — Database Manager
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("<div style='font-size:1.1rem;font-weight:700;color:#2e55c8;margin-bottom:12px;'>🗄️ Database Manager</div>", unsafe_allow_html=True)

    db_configs = load_db_configs()

    # ── Add / Edit DB form ─────────────────────────────────────────────────
    with st.expander("➕ Add New Database", expanded=len(db_configs) == 0):
        new_name = st.text_input("DB Alias (unique name)", key="new_name",
                                 placeholder="e.g. PROD_DB1")
        new_user = st.text_input("Username", key="new_user")
        new_pwd  = st.text_input("Password", type="password", key="new_pwd")
        new_dsn  = st.text_input("DSN (host:port/service)", key="new_dsn",
                                 placeholder="myhost:1521/ORCL")

        col_add, col_test = st.columns(2)
        with col_add:
            if st.button("➕ Add", use_container_width=True):
                if not new_name or not new_user or not new_pwd or not new_dsn:
                    st.error("All fields required.")
                elif any(d["name"] == new_name for d in db_configs):
                    st.error(f"'{new_name}' already exists.")
                else:
                    db_configs.append({"name": new_name, "user": new_user,
                                       "password": new_pwd, "dsn": new_dsn})
                    save_db_configs(db_configs)
                    st.success(f"'{new_name}' added!")
                    st.session_state.pop("summary_df",  None)
                    st.session_state.pop("module_df",   None)
                    st.rerun()
        with col_test:
            if st.button("🔌 Test", use_container_width=True):
                if new_user and new_pwd and new_dsn:
                    ok, msg = test_connection({"name": new_name,"user": new_user,
                                               "password": new_pwd,"dsn": new_dsn})
                    (st.success if ok else st.error)(msg)
                else:
                    st.warning("Fill in credentials first.")

    st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)

    # ── DB list with remove ────────────────────────────────────────────────
    if db_configs:
        st.markdown(f"<div style='color:#5a7ab0;font-size:0.8rem;margin-bottom:6px;'>{len(db_configs)} database(s) configured</div>", unsafe_allow_html=True)
        for idx, cfg in enumerate(db_configs):
            cols = st.columns([3, 1])
            cols[0].markdown(
                f"<div style='color:#1e2a3a;font-size:0.85rem;font-weight:600;'>{cfg['name']}</div>"
                f"<div style='color:#5a7ab0;font-size:0.75rem;'>{cfg['dsn']}</div>",
                unsafe_allow_html=True,
            )
            if cols[1].button("🗑️", key=f"del_{idx}", help=f"Remove {cfg['name']}"):
                db_configs.pop(idx)
                save_db_configs(db_configs)
                st.session_state.pop("summary_df", None)
                st.session_state.pop("module_df",  None)
                st.rerun()
    else:
        st.info("No databases configured yet. Add one above.")

    st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)

    # ── DB selection for queries ───────────────────────────────────────────
    if db_configs:
        db_names = [d["name"] for d in db_configs]
        selected_db_names = st.multiselect(
            "📡 Query these databases",
            db_names,
            default=db_names,
            key="selected_dbs",
        )

        st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-size:0.85rem;font-weight:700;color:#2e55c8;margin-bottom:8px;'>🔎 Filters (optional)</div>", unsafe_allow_html=True)
        sql_id_filter = st.text_input(
            "SQL ID",
            placeholder="e.g. 3g8fqh2v9fk7u",
            help="Filter to a specific SQL statement. Oracle's unique 13-character ID for each query.",
        ).strip() or None
        schema_filter = st.text_input(
            "Owner Schema",
            placeholder="e.g. HR, SCOTT, APP_USER",
            help="Filter by the database user/schema that owns the SQL (Oracle calls this the parsing schema).",
        ).strip().upper() or None

        module_options  = ["All Modules"] + st.session_state.get("module_list", [])
        sel_mod_sidebar = st.selectbox(
            "📦 Application Module",
            module_options,
            help="Filter by the application that ran the SQL. "
                 "'(unknown)' means the app did not set a module name when connecting.",
            key="module_sidebar",
        )
        module_filter   = None if sel_mod_sidebar == "All Modules" else sel_mod_sidebar

        st.markdown("<hr style='margin:10px 0;'>", unsafe_allow_html=True)
        refresh = st.button("🚀 Load Dashboard", use_container_width=True)

        if st.session_state.get("connected"):
            st.markdown("<div style='text-align:center;margin-top:8px;'><span style='background:#f0fdf4;color:#16a34a;border:1px solid #86efac;padding:4px 12px;border-radius:20px;font-size:0.78rem;font-weight:600;'>✅ Connected</span></div>", unsafe_allow_html=True)

        with st.expander("🔐 Required Oracle Privileges"):
            st.code(
                "GRANT SELECT ON V_$SQL TO <user>;\n"
                "GRANT SELECT ON V_$SQL_PLAN TO <user>;\n"
                "GRANT SELECT ON V_$SQLAREA TO <user>;\n"
                "GRANT ADVISOR TO <user>;\n"
                "GRANT EXECUTE ON DBMS_SQLTUNE TO <user>;\n"
                "GRANT SELECT_CATALOG_ROLE TO <user>;",
                language="sql",
            )
    else:
        refresh         = False
        selected_db_names = []
        sql_id_filter   = None
        schema_filter   = None
        module_filter   = None


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — Guard: need at least one DB
# ══════════════════════════════════════════════════════════════════════════════

if not db_configs:
    st.markdown("""
    <div style="text-align:center;padding:60px 0;">
        <div style="font-size:4rem;">🗄️</div>
        <div style="font-size:1.4rem;font-weight:600;color:#2e55c8;margin-top:12px;">
            No Databases Configured
        </div>
        <div style="color:#5a7ab0;margin-top:8px;">
            Use <b>➕ Add New Database</b> in the sidebar to add your Oracle connections.
        </div>
    </div>""", unsafe_allow_html=True)
    st.stop()

if not selected_db_names:
    st.warning("Please select at least one database from the sidebar to query.")
    st.stop()

selected_cfgs = [d for d in db_configs if d["name"] in selected_db_names]

# Persist state on Load Dashboard click
if refresh:
    st.session_state.update({
        "connected":      True,
        "sql_id_filter":  sql_id_filter,
        "schema_filter":  schema_filter,
        "selected_cfgs":  selected_cfgs,
    })
    for key in ("summary_df", "module_df", "module_list", "dash_chat"):
        st.session_state.pop(key, None)

# Invalidate cache on module change
prev_mod = st.session_state.get("active_module_filter")
if prev_mod != module_filter:
    st.session_state.pop("summary_df", None)
    st.session_state["active_module_filter"] = module_filter

if "selected_cfgs" not in st.session_state:
    st.markdown("""
    <div style="text-align:center;padding:60px 0;">
        <div style="font-size:4rem;">🗄️</div>
        <div style="font-size:1.4rem;font-weight:600;color:#2e55c8;margin-top:12px;">
            Ready to Load
        </div>
        <div style="color:#5a7ab0;margin-top:8px;">
            Select databases in the sidebar and click <b>Load Dashboard</b>.
        </div>
    </div>""", unsafe_allow_html=True)
    st.stop()

active_cfgs = st.session_state["selected_cfgs"]
sql_id_filter = st.session_state.get("sql_id_filter")
schema_filter = st.session_state.get("schema_filter")

binds = {
    "sql_id_filter": sql_id_filter,
    "schema_filter": schema_filter,
    "module_filter": module_filter,
}

# ── Fetch summary data ────────────────────────────────────────────────────────
if "summary_df" not in st.session_state:
    st.session_state["query_errors"] = []
    with st.spinner(f"Querying {len(active_cfgs)} database(s) in parallel…"):
        st.session_state["summary_df"] = query_all_dbs(active_cfgs, SQL_SUMMARY_QUERY, binds)

    if st.session_state["query_errors"]:
        with st.expander(f"⚠️ {len(st.session_state['query_errors'])} DB(s) had errors"):
            st.dataframe(pd.DataFrame(st.session_state["query_errors"]),
                         use_container_width=True, hide_index=True)

if "module_df" not in st.session_state:
    raw_mod = query_all_dbs(active_cfgs, MODULE_SUMMARY_QUERY, {})
    if not raw_mod.empty:
        st.session_state["module_df"] = (
            raw_mod.groupby("MODULE", as_index=False).agg(
                TOTAL_SQLS=("TOTAL_SQLS", "sum"),
                FLUCTUATING_SQLS=("FLUCTUATING_SQLS", "sum"),
                TOTAL_EXECUTIONS=("TOTAL_EXECUTIONS", "sum"),
                AVG_ELAPSED_SEC=("AVG_ELAPSED_SEC", "mean"),
            ).sort_values("FLUCTUATING_SQLS", ascending=False)
        )
        st.session_state["module_list"] = st.session_state["module_df"]["MODULE"].tolist()
    else:
        st.session_state["module_df"]   = pd.DataFrame()
        st.session_state["module_list"] = []

summary_df = st.session_state["summary_df"]
module_df  = st.session_state["module_df"]

# Oracle returns COUNT/SUM aggregates as Decimal; coerce to int/float so
# numeric operations (nlargest, comparisons, plotting) work correctly.
for _col in ("PLAN_COUNT", "TOTAL_EXECUTIONS", "ELAPSED_TIME_S", "CPU_TIME_S",
             "BUFFER_GETS", "DISK_READS", "ROWS_PROCESSED"):
    if _col in summary_df.columns:
        summary_df[_col] = pd.to_numeric(summary_df[_col], errors="coerce")

if summary_df.empty:
    st.warning("No SQL data returned from any database with the selected filters.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING GUIDE
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("👋 New here? Click to learn how to use this dashboard", expanded=False):
    st.markdown("""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">

<div style="background:#f0f7ff;border-left:4px solid #4f6ef7;border-radius:8px;padding:16px;">
<b style="color:#2e55c8;font-size:1rem;">🤔 What is SQL Plan Fluctuation?</b><br><br>
<span style="color:#3a4f6e;font-size:0.9rem;line-height:1.7;">
Every time Oracle runs a SQL query, it creates an <b>execution plan</b> — a step-by-step 
strategy for fetching data. The Oracle Optimizer usually picks the fastest plan.<br><br>
<b>Plan fluctuation</b> happens when Oracle keeps switching between different plans for the 
same SQL — this often causes sudden slowdowns that are hard to diagnose.<br><br>
🔴 <b>Fluctuating SQL</b> = the optimizer changed its plan one or more times<br>
🟢 <b>Stable SQL</b> = always uses the same plan (safe)
</span>
</div>

<div style="background:#f0fff4;border-left:4px solid #22c55e;border-radius:8px;padding:16px;">
<b style="color:#15803d;font-size:1rem;">🚀 Quick Start — 4 Steps</b><br><br>
<span style="color:#3a4f6e;font-size:0.9rem;line-height:1.9;">
<b>Step 1</b> — Open the sidebar (← left) and click <b>➕ Add New Database</b><br>
<b>Step 2</b> — Enter your DB name, username, password, and DSN (host:port/service)<br>
<b>Step 3</b> — Tick the databases you want to query, then click <b>Load Dashboard</b><br>
<b>Step 4</b> — Start in the <b>📊 Overview</b> tab — look for 🔴 red SQLs to investigate
</span>
</div>

<div style="background:#fff8f0;border-left:4px solid #f59e0b;border-radius:8px;padding:16px;">
<b style="color:#d97706;font-size:1rem;">📖 Tab Guide</b><br><br>
<span style="color:#3a4f6e;font-size:0.9rem;line-height:1.8;">
📊 <b>Overview</b> — Health summary for all databases<br>
🌐 <b>Cross-DB</b> — Side-by-side DB comparison<br>
📦 <b>Module Drilldown</b> — Which application is causing issues?<br>
⚡ <b>Plan Analysis</b> — Compare execution plans for one SQL<br>
🧠 <b>SQL Tuning Advisor</b> — Get Oracle's fix recommendations<br>
📈 <b>Execution History</b> — How did a SQL perform over time?<br>
📋 <b>AWR Report</b> — Full database performance report<br>
💬 <b>Ask</b> — Ask questions in plain English
</span>
</div>

<div style="background:#fdf0ff;border-left:4px solid #a855f7;border-radius:8px;padding:16px;">
<b style="color:#7c3aed;font-size:1rem;">💡 Key Terms Explained</b><br><br>
<span style="color:#3a4f6e;font-size:0.9rem;line-height:1.8;">
<b>SQL_ID</b> — Oracle's unique ID for a SQL statement (e.g. <code>3g8fq2v9fk7u</code>)<br>
<b>Plan Hash Value</b> — A number that uniquely identifies one execution plan<br>
<b>AWR</b> — Automatic Workload Repository: Oracle's performance history store<br>
<b>Module</b> — The application that ran the SQL (e.g. JDBC, SQL*Plus, your app)<br>
<b>Buffer Gets</b> — Memory reads (logical I/O) — lower is better<br>
<b>Disk Reads</b> — Storage reads (physical I/O) — much more expensive than memory<br>
<b>Avg Elapsed</b> — Average time in seconds to run the SQL — lower is better
</span>
</div>

</div>
""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB LAYOUT
# ══════════════════════════════════════════════════════════════════════════════
tab_overview, tab_crossdb, tab_module, tab_plan, tab_advisor, tab_history, tab_awr, tab_ask = st.tabs([
    "📊 Overview",
    "🌐 Cross-DB Comparison",
    "📦 Module Drilldown",
    "⚡ Plan Analysis",
    "🧠 SQL Tuning Advisor",
    "📈 Execution History",
    "📋 AWR Report",
    "💬 Ask the Dashboard",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Overview
# ══════════════════════════════════════════════════════════════════════════════
with tab_overview:
    st.info(
        "🔍 **What you're looking at:** Each SQL query in Oracle has an execution plan. "
        "When Oracle keeps switching plans for the same SQL it can cause sudden slowdowns. "
        "**🔴 Red = problem SQLs** that changed plans. **🟢 Green = stable and safe.** "
        "Start by investigating any 🔴 fluctuating SQLs using the Plan Analysis or Tuning Advisor tab.",
        icon=None,
    )

    fluctuating_df = summary_df[summary_df["PLAN_COUNT"] > 1]
    stable_df      = summary_df[summary_df["PLAN_COUNT"] == 1]
    total          = len(summary_df)
    fluct_pct      = f"{len(fluctuating_df)/total*100:.0f}% of total" if total else "0%"
    stable_pct     = f"{len(stable_df)/total*100:.0f}% of total"      if total else "0%"
    # Compute worst SQL upfront so the KPI tile and the button share the same values
    _worst_idx     = summary_df["PLAN_COUNT"].idxmax() if not summary_df.empty else None
    worst_plan_cnt = int(summary_df.loc[_worst_idx, "PLAN_COUNT"]) if _worst_idx is not None else 0
    _worst_sql_id  = summary_df.loc[_worst_idx, "SQL_ID"]      if _worst_idx is not None else ""
    _worst_db      = summary_df.loc[_worst_idx, "DATABASE"]     if _worst_idx is not None else ""

    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        st.metric(
            "Databases Queried", len(active_cfgs),
            help="Number of Oracle databases successfully queried in this load.",
        )
    with c2:
        st.metric(
            "Total SQL IDs", f"{total:,}",
            help="Total distinct SQL statements found across all selected databases.",
        )
        if st.button(
            "📋 View all SQLs", key="btn_all_sqls", use_container_width=True,
            help="Open the Full SQL Summary Table showing all SQL IDs.",
        ):
            st.session_state["ov_fluct_only"] = False
            st.session_state["ov_sql_search"] = ""
            st.session_state["ov_db_filter"]  = []
            st.session_state["ov_jump_table"] = True
            st.rerun()
    with c3:
        st.metric(
            "🔴 Plan Fluctuation SQLs", f"{len(fluctuating_df):,}",
            delta=f"-{fluct_pct}" if len(fluctuating_df) > 0 else "✅ None",
            delta_color="inverse",
            help="SQLs that used more than one execution plan — these are your problem candidates.",
        )
        if len(fluctuating_df) > 0:
            if st.button(
                "🔍 View fluctuating SQLs", key="btn_fluct", use_container_width=True,
                help="Open the summary table filtered to show only SQLs with plan instability.",
            ):
                st.session_state["ov_fluct_only"] = True
                st.session_state["ov_sql_search"] = ""
                st.session_state["ov_db_filter"]  = []
                st.session_state["ov_jump_table"] = True
                st.rerun()
    with c4:
        st.metric(
            "🟢 Stable SQLs", f"{len(stable_df):,}",
            delta=stable_pct,
            delta_color="normal",
            help="SQLs that always used the same plan — no action needed.",
        )
        if st.button(
            "📋 View stable SQLs", key="btn_stable", use_container_width=True,
            help="Open the summary table filtered to show only stable SQLs.",
        ):
            st.session_state["ov_fluct_only"] = False
            st.session_state["ov_sql_search"] = ""
            st.session_state["ov_db_filter"]  = []
            st.session_state["ov_jump_table"] = "stable"
            st.rerun()
    with c5:
        _worst_urgency = (
            "⚠️ Review urgently" if worst_plan_cnt > 3
            else ("ℹ️ Monitor" if worst_plan_cnt > 1 else "✅ All SQLs stable")
        )
        st.metric(
            "Plans Used by Worst SQL",
            worst_plan_cnt,
            delta=_worst_urgency,
            delta_color="inverse" if worst_plan_cnt > 1 else "off",
            help=(
                f"This is the number of different execution plans used by the single most-unstable SQL "
                f"({_worst_sql_id} on {_worst_db}). "
                f"It is NOT a count of how many SQLs are bad — it tells you how many plans "
                f"1 SQL has switched between. Anything above 1 means plan instability; above 3 is urgent."
            ),
        )
        if worst_plan_cnt > 1:
            if st.button(
                "🔍 View worst SQL", key="btn_worst", use_container_width=True,
                help=f"Filter the table to SQL ID {_worst_sql_id} — the one SQL that used {worst_plan_cnt} different plans.",
            ):
                st.session_state["ov_sql_search"]      = _worst_sql_id
                st.session_state["ov_worst_plan_cnt"]  = worst_plan_cnt
                st.session_state["ov_worst_sql_id"]    = _worst_sql_id
                st.session_state["ov_worst_db"]        = _worst_db
                st.session_state["ov_fluct_only"]      = False
                st.session_state["ov_db_filter"]       = []
                st.session_state["ov_jump_table"]      = True
                st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    col_pie, col_bar = st.columns([1, 2])
    with col_pie:
        fig_pie = px.pie(
            pd.DataFrame({"Status": ["🟢 Stable","🔴 Fluctuating"],
                          "Count":  [len(stable_df), len(fluctuating_df)]}),
            names="Status", values="Count", hole=0.55,
            color="Status",
            color_discrete_map={"🟢 Stable":"#4ade80","🔴 Fluctuating":"#f87171"},
            title="SQL Stability (All DBs)",
        )
        fig_pie.update_layout(paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                              font_color="#1e2a3a", title_font_color="#2e55c8",
                              legend=dict(font=dict(color="#1e2a3a")),
                              margin=dict(t=40,b=10,l=10,r=10))
        fig_pie.update_traces(textfont_color="#1e2a3a")
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_bar:
        n_dbs = summary_df["DATABASE"].nunique()
        # Take top 5 per database so every DB is always represented in the chart
        top_per_db = (
            summary_df
            .sort_values("PLAN_COUNT", ascending=False)
            .groupby("DATABASE", group_keys=False)
            .head(5)
            .sort_values("PLAN_COUNT", ascending=False)
        )
        n_bars = len(top_per_db)
        chart_title = (
            f"Top 5 SQLs per Database by Plan Count — {n_dbs} DB(s) shown"
            if n_dbs > 1 else "Top SQLs by Plan Count"
        )
        # Build a combined x-axis label "SQL_ID (DB)" to avoid collisions across DBs
        top_per_db = top_per_db.copy()
        top_per_db["SQL_LABEL"] = top_per_db["SQL_ID"] + "\n(" + top_per_db["DATABASE"] + ")"
        fig_bar = px.bar(
            top_per_db, x="SQL_LABEL", y="PLAN_COUNT",
            color="DATABASE", text="PLAN_COUNT",
            title=chart_title,
            labels={"PLAN_COUNT": "# Plans", "SQL_LABEL": "SQL ID (Database)"},
        )
        bar_height = max(380, min(600, n_bars * 28 + 120))
        fig_bar.update_layout(
            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
            font_color="#1e2a3a", title_font_color="#2e55c8",
            height=bar_height,
            xaxis=dict(
                tickfont=dict(color="#5a7ab0", size=9),
                gridcolor="#dce6fb", tickangle=-35,
            ),
            yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
            legend=dict(
                font=dict(color="#1e2a3a"), bgcolor="#ffffff",
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            ),
            margin=dict(t=60, b=100, l=10, r=10),
        )
        fig_bar.update_traces(textposition="outside", textfont_color="#1e2a3a")
        st.plotly_chart(fig_bar, use_container_width=True)

    # Determine if we should auto-open and what filter to pre-apply from a tile click
    _jump = st.session_state.get("ov_jump_table", False)
    # For "stable" jump — pre-apply stable-only filter via a temporary flag
    _stable_jump = (_jump == "stable")
    if _stable_jump:
        st.session_state["ov_jump_table"] = False  # clear immediately

    # Show a prominent call-to-action banner when jumping from a tile
    if _jump and not _stable_jump:
        _saved_worst_cnt    = st.session_state.pop("ov_worst_plan_cnt", None)
        _saved_worst_sql_id = st.session_state.pop("ov_worst_sql_id",   None)
        _saved_worst_db     = st.session_state.pop("ov_worst_db",       None)

        if _saved_worst_cnt is not None:
            # Special explanatory banner for "View worst SQL" clicks
            st.warning(
                f"📊 **Worst SQL filter applied** — showing **SQL ID: {_saved_worst_sql_id}** "
                f"(Database: **{_saved_worst_db}**).\n\n"
                f"The table below shows **1 SQL** (one row). "
                f"The number **{_saved_worst_cnt}** in the KPI above means this single SQL "
                f"has switched between **{_saved_worst_cnt} different execution plans** — "
                f"that is what makes it the worst. It is not a count of how many SQLs are shown here.",
                icon="⚠️",
            )
        else:
            _active_filter = (
                "🔍 Showing **fluctuating SQLs only**"
                if st.session_state.get("ov_fluct_only")
                else f"🔍 Filtered to SQL ID: **{st.session_state.get('ov_sql_search', '')}**"
                if st.session_state.get("ov_sql_search", "").strip()
                else "📋 Showing **all SQLs**"
            )
            st.success(f"{_active_filter} — table opened below ↓", icon="👇")
        st.session_state["ov_jump_table"] = False  # clear flag after reading

    with st.expander("📋 Full SQL Summary Table", expanded=bool(_jump)):
        # ── Search filters ────────────────────────────────────────────────────
        db_options = sorted(summary_df["DATABASE"].unique().tolist())
        fs_col1, fs_col2, fs_col3, fs_col4 = st.columns([1.5, 2, 1, 1])
        with fs_col1:
            sel_dbs = st.multiselect(
                "Filter by Database",
                options=db_options,
                default=st.session_state.get("ov_db_filter", []),
                placeholder="All databases",
                key="ov_db_filter",
            )
        with fs_col2:
            sql_search = st.text_input(
                "Search SQL ID (partial match)",
                placeholder="e.g. 3g5qh…",
                key="ov_sql_search",
            )
        with fs_col3:
            show_fluct_only = st.checkbox(
                "🔴 Fluctuating only", value=False, key="ov_fluct_only",
            )
        with fs_col4:
            show_stable_only = st.checkbox(
                "🟢 Stable only", value=_stable_jump, key="ov_stable_only",
            )

        # ── Reset button ──────────────────────────────────────────────────────
        if st.button("↺ Clear filters", key="ov_clear_filters"):
            st.session_state["ov_db_filter"]   = []
            st.session_state["ov_sql_search"]  = ""
            st.session_state["ov_fluct_only"]  = False
            st.session_state["ov_stable_only"] = False
            st.rerun()

        # ── Apply filters ─────────────────────────────────────────────────────
        filtered_summary = summary_df.copy()
        if sel_dbs:
            filtered_summary = filtered_summary[filtered_summary["DATABASE"].isin(sel_dbs)]
        if sql_search.strip():
            filtered_summary = filtered_summary[
                filtered_summary["SQL_ID"].str.contains(sql_search.strip(), case=False, na=False)
            ]
        if show_fluct_only:
            filtered_summary = filtered_summary[filtered_summary["PLAN_COUNT"] > 1]
        if show_stable_only and not show_fluct_only:
            filtered_summary = filtered_summary[filtered_summary["PLAN_COUNT"] == 1]

        # ── Rename raw Oracle column names to human-readable labels ───────────
        _col_rename = {
            "DATABASE":             "Database",
            "SQL_ID":               "SQL ID",
            "PARSING_SCHEMA_NAME":  "Owner Schema",
            "MODULE":               "Application Module",
            "ACTION":               "Action",
            "PLAN_COUNT":           "# Plans (Higher = Problem)",
            "TOTAL_EXECUTIONS":     "Total Executions",
            "FIRST_LOAD_TIME":      "First Seen",
            "LAST_ACTIVE_TIME":     "Last Active",
        }
        display_df  = filtered_summary.rename(columns=_col_rename)
        _grad_col   = _col_rename.get("PLAN_COUNT", "PLAN_COUNT")
        _fluct_disp = int((filtered_summary["PLAN_COUNT"] > 1).sum())
        _stable_disp= int((filtered_summary["PLAN_COUNT"] == 1).sum())

        _plan_col_label = _col_rename.get("PLAN_COUNT", "# Plans (Higher = Problem)")
        st.caption(
            f"Showing **{len(filtered_summary):,}** of **{len(summary_df):,}** SQL entries  |  "
            f"🔴 {_fluct_disp:,} fluctuating (plan count > 1)  🟢 {_stable_disp:,} stable  |  "
            f"The **'{_plan_col_label}'** column shows how many different plans each SQL used — "
            f"each row = 1 SQL, not 1 plan"
        )
        st.dataframe(
            display_df.style.background_gradient(subset=[_grad_col], cmap="RdYlGn_r"),
            use_container_width=True, hide_index=True,
        )

        # ── Export ────────────────────────────────────────────────────────────
        st.download_button(
            "⬇️ Export filtered results as CSV",
            data=filtered_summary.to_csv(index=False),
            file_name="sql_summary_filtered.csv",
            mime="text/csv",
            key="ov_export_csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Cross-DB Comparison
# ══════════════════════════════════════════════════════════════════════════════
with tab_crossdb:
    st.markdown('<div class="section-header">🌐 Database-by-Database Comparison</div>',
                unsafe_allow_html=True)
    st.info(
        "🌐 **What this tab does:** Compares all your databases side by side. "
        "**Health %** shows how many SQLs are stable — 100% is perfect, lower means more plan instability. "
        "Use this tab to quickly identify *which database* needs attention first.",
    )

    db_agg = (
        summary_df.groupby("DATABASE", as_index=False)
        .agg(
            TOTAL_SQLS        =("SQL_ID",       "count"),
            FLUCTUATING_SQLS  =("PLAN_COUNT",   lambda x: (x > 1).sum()),
            STABLE_SQLS       =("PLAN_COUNT",   lambda x: (x == 1).sum()),
            TOTAL_EXECUTIONS  =("TOTAL_EXECUTIONS", "sum"),
            MAX_PLAN_COUNT    =("PLAN_COUNT",   "max"),
            AVG_PLAN_COUNT    =("PLAN_COUNT",   "mean"),
        )
        .sort_values("FLUCTUATING_SQLS", ascending=False)
    )
    db_agg["HEALTH_%"] = (
        db_agg["STABLE_SQLS"] / db_agg["TOTAL_SQLS"] * 100
    ).round(1)
    db_agg["STATUS"] = db_agg["FLUCTUATING_SQLS"].apply(
        lambda x: "🔴 Issues" if x > 0 else "🟢 Healthy"
    )

    # KPIs
    healthy_dbs = int((db_agg["FLUCTUATING_SQLS"] == 0).sum())
    issue_dbs   = int((db_agg["FLUCTUATING_SQLS"] >  0).sum())
    x1, x2, x3, x4 = st.columns(4)
    x1.metric("Total DBs Queried",   len(db_agg))
    x2.metric("🟢 Healthy DBs",      healthy_dbs)
    x3.metric("🔴 DBs with Issues",  issue_dbs)
    x4.metric("Total Fluctuating SQLs", f"{int(db_agg['FLUCTUATING_SQLS'].sum()):,}")

    st.markdown("<br>", unsafe_allow_html=True)

    # Stacked bar — stable vs fluctuating per DB
    fig_db = go.Figure()
    fig_db.add_trace(go.Bar(
        name="🔴 Fluctuating", x=db_agg["DATABASE"],
        y=db_agg["FLUCTUATING_SQLS"], marker_color="#f87171",
        text=db_agg["FLUCTUATING_SQLS"], textposition="auto",
        textfont=dict(color="#1e2a3a"),
    ))
    fig_db.add_trace(go.Bar(
        name="🟢 Stable", x=db_agg["DATABASE"],
        y=db_agg["STABLE_SQLS"], marker_color="#4ade80",
        text=db_agg["STABLE_SQLS"], textposition="auto",
        textfont=dict(color="#1e2a3a"),
    ))
    fig_db.update_layout(
        barmode="stack",
        title="SQL Plan Health per Database",
        paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
        font_color="#1e2a3a", title_font_color="#2e55c8",
        xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb", tickangle=-30),
        yaxis=dict(title="SQL Count", tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
        legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
        margin=dict(t=40, b=80, l=10, r=10),
    )
    st.plotly_chart(fig_db, use_container_width=True)

    # Health % gauge-style bar
    fig_health = px.bar(
        db_agg.sort_values("HEALTH_%"),
        x="HEALTH_%", y="DATABASE", orientation="h",
        color="HEALTH_%",
        color_continuous_scale=["#f87171", "#facc15", "#4ade80"],
        range_color=[0, 100],
        text="HEALTH_%",
        title="SQL Plan Health % per Database (100% = all stable)",
        labels={"HEALTH_%": "Health %", "DATABASE": "Database"},
    )
    fig_health.update_layout(
        paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
        font_color="#1e2a3a", title_font_color="#2e55c8",
        xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb", range=[0,105]),
        yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
        coloraxis_showscale=False,
        margin=dict(t=40, b=20, l=10, r=10),
        height=max(300, len(db_agg) * 35),
    )
    fig_health.update_traces(texttemplate="%{text:.1f}%", textposition="outside",
                              textfont_color="#1e2a3a")
    st.plotly_chart(fig_health, use_container_width=True)

    # Summary table
    st.markdown('<div class="section-header">📋 Database Summary Table</div>',
                unsafe_allow_html=True)

    def _db_row_style(row):
        c = "#fff1f0" if row["FLUCTUATING_SQLS"] > 0 else "#f0fdf4"
        return [f"background-color:{c}"] * len(row)

    st.dataframe(
        db_agg[["STATUS","DATABASE","TOTAL_SQLS","FLUCTUATING_SQLS",
                "STABLE_SQLS","HEALTH_%","MAX_PLAN_COUNT","TOTAL_EXECUTIONS"]]
        .style.apply(_db_row_style, axis=1)
        .format({"TOTAL_EXECUTIONS":"{:,.0f}", "HEALTH_%":"{:.1f}%",
                 "AVG_PLAN_COUNT":"{:.2f}"}),
        use_container_width=True, hide_index=True,
    )

    # Per-DB fluctuating SQL list
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-header">🔎 Fluctuating SQLs — Select Database</div>',
                unsafe_allow_html=True)
    sel_db = st.selectbox(
        "Database",
        db_agg["DATABASE"].tolist(),
        format_func=lambda d: (
            f"🔴  {d}  ({int(db_agg.loc[db_agg['DATABASE']==d,'FLUCTUATING_SQLS'].values[0])} issues)"
            if int(db_agg.loc[db_agg["DATABASE"]==d,"FLUCTUATING_SQLS"].values[0]) > 0
            else f"🟢  {d}  (healthy)"
        ),
        key="crossdb_sel_db",
    )
    db_detail = summary_df[
        (summary_df["DATABASE"] == sel_db) & (summary_df["PLAN_COUNT"] > 1)
    ].copy()
    if db_detail.empty:
        st.success(f"✅ **{sel_db}** has no plan fluctuations.")
    else:
        st.dataframe(
            db_detail[["SQL_ID","MODULE","ACTION","PLAN_COUNT",
                        "TOTAL_EXECUTIONS","LAST_ACTIVE_TIME"]]
            .style.background_gradient(subset=["PLAN_COUNT"], cmap="Reds"),
            use_container_width=True, hide_index=True,
        )
        csv_db = db_detail.to_csv(index=False)
        st.download_button(
            f"⬇️ Export {sel_db} Fluctuating SQLs",
            data=csv_db,
            file_name=f"fluctuating_{sel_db}.csv",
            mime="text/csv",
            use_container_width=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Module Drilldown (Per-Database + Compare)
# ══════════════════════════════════════════════════════════════════════════════
with tab_module:
    st.markdown('<div class="section-header">📦 Module Drilldown — Per Database</div>',
                unsafe_allow_html=True)
    st.info(
        "📦 **What this tab does:** Groups your SQLs by the *application module* that ran them "
        "(e.g. your Java app, a batch job, SQL*Plus). Select a database to see its module "
        "breakdown, or switch to **Compare Mode** to see two databases side by side for the "
        "same module. 💡 **(unknown)** = the application did not set a module name when connecting.",
    )

    # ── Build per-database module stats from summary_df ────────────────────────
    _per_db_mod = (
        summary_df.groupby(["DATABASE", "MODULE"], as_index=False)
        .agg(
            TOTAL_SQLS      =("SQL_ID",           "count"),
            FLUCTUATING_SQLS=("PLAN_COUNT",        lambda x: int((x > 1).sum())),
            STABLE_SQLS     =("PLAN_COUNT",        lambda x: int((x == 1).sum())),
            TOTAL_EXECUTIONS=("TOTAL_EXECUTIONS",  "sum"),
            MAX_PLAN_COUNT  =("PLAN_COUNT",        "max"),
        )
    )
    _per_db_mod["HEALTH_%"] = (
        _per_db_mod["STABLE_SQLS"] / _per_db_mod["TOTAL_SQLS"] * 100
    ).round(1)
    _per_db_mod["STATUS"] = _per_db_mod["FLUCTUATING_SQLS"].apply(
        lambda x: "🔴 Issues" if x > 0 else "🟢 Stable"
    )
    _all_dbs     = sorted(summary_df["DATABASE"].unique().tolist())
    _all_modules = sorted(summary_df["MODULE"].dropna().unique().tolist())

    # ── Mode toggle ────────────────────────────────────────────────────────────
    mod_mode = st.radio(
        "View mode",
        ["🗄️ Single Database", "⚖️ Compare Two Databases"],
        horizontal=True,
        key="mod_view_mode",
        help="Single Database: see all modules for one DB.  "
             "Compare: pick two databases and one module to compare them side by side.",
    )
    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # MODE 1 — Single Database
    # ══════════════════════════════════════════════════════════════════════════
    if mod_mode == "🗄️ Single Database":

        # DB selector — databases with most issues first
        _db_issue_order = (
            _per_db_mod.groupby("DATABASE")["FLUCTUATING_SQLS"].sum()
            .sort_values(ascending=False).index.tolist()
        )
        sel_mod_db = st.selectbox(
            "Select Database",
            _db_issue_order,
            format_func=lambda d: (
                f"🔴  {d}  ({int(_per_db_mod[_per_db_mod['DATABASE']==d]['FLUCTUATING_SQLS'].sum())} module(s) with issues)"
                if int(_per_db_mod[_per_db_mod["DATABASE"]==d]["FLUCTUATING_SQLS"].sum()) > 0
                else f"🟢  {d}  (all modules stable)"
            ),
            key="mod_single_db",
            help="Databases with the most module-level plan issues are listed first.",
        )

        _db_mods = _per_db_mod[_per_db_mod["DATABASE"] == sel_mod_db].copy()
        _db_mods = _db_mods.sort_values("FLUCTUATING_SQLS", ascending=False)

        # ── KPI tiles ──────────────────────────────────────────────────────────
        _tot_m  = len(_db_mods)
        _iss_m  = int((_db_mods["FLUCTUATING_SQLS"] > 0).sum())
        _stb_m  = _tot_m - _iss_m
        _health = round(_db_mods["STABLE_SQLS"].sum() / _db_mods["TOTAL_SQLS"].sum() * 100, 1) if _tot_m else 0

        km1, km2, km3, km4 = st.columns(4)
        km1.metric("Modules Found",         _tot_m,
                   help="Total distinct application modules for this database.")
        km2.metric("🔴 Modules with Issues", _iss_m,
                   delta=f"{_iss_m/_tot_m*100:.0f}% of modules" if _tot_m else "0%",
                   delta_color="inverse")
        km3.metric("🟢 Fully Stable Modules", _stb_m)
        km4.metric("Overall DB Health %",   f"{_health:.1f}%",
                   delta="✅ Healthy" if _health == 100 else ("⚠️ Needs attention" if _health >= 60 else "🔴 Critical"),
                   delta_color="off")

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Stacked bar: modules for this DB ───────────────────────────────────
        _top_mods = _db_mods.head(20)
        fig_mdb = go.Figure()
        fig_mdb.add_trace(go.Bar(
            name="🔴 Fluctuating", x=_top_mods["MODULE"],
            y=_top_mods["FLUCTUATING_SQLS"], marker_color="#f87171",
            text=_top_mods["FLUCTUATING_SQLS"], textposition="auto",
            textfont=dict(color="#1e2a3a"),
        ))
        fig_mdb.add_trace(go.Bar(
            name="🟢 Stable", x=_top_mods["MODULE"],
            y=_top_mods["STABLE_SQLS"], marker_color="#4ade80",
            text=_top_mods["STABLE_SQLS"], textposition="auto",
            textfont=dict(color="#1e2a3a"),
        ))
        fig_mdb.update_layout(
            barmode="stack",
            title=f"Module Health — {sel_mod_db}",
            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
            font_color="#1e2a3a", title_font_color="#2e55c8",
            xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb", tickangle=-30),
            yaxis=dict(title="SQL Count", tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
            legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
            margin=dict(t=40, b=80, l=10, r=10),
        )
        st.plotly_chart(fig_mdb, use_container_width=True)

        # ── Module summary table ───────────────────────────────────────────────
        st.markdown('<div class="section-header">📋 Module Summary Table</div>',
                    unsafe_allow_html=True)
        _disp_db_mods = _db_mods[["STATUS","MODULE","TOTAL_SQLS","FLUCTUATING_SQLS",
                                    "STABLE_SQLS","HEALTH_%","MAX_PLAN_COUNT",
                                    "TOTAL_EXECUTIONS"]].copy()
        _disp_db_mods.columns = ["Status","Module","Total SQLs","Fluctuating",
                                   "Stable","Health %","Max Plans","Total Executions"]
        st.dataframe(
            _disp_db_mods.style.apply(
                lambda r: [f"background-color:{'#fff1f0' if r['Fluctuating']>0 else '#f0fdf4'}"] * len(r),
                axis=1,
            ).format({"Health %": "{:.1f}%", "Total Executions": "{:,.0f}"}),
            use_container_width=True, hide_index=True,
        )

        # ── Per-module SQL drilldown ───────────────────────────────────────────
        st.divider()
        st.markdown('<div class="section-header">🔎 Module SQL Drilldown</div>',
                    unsafe_allow_html=True)
        st.caption("Select a module below to see all its SQLs for this database.")

        _db_mod_list = _db_mods["MODULE"].tolist()
        if not _db_mod_list:
            st.info("No module data for this database.")
        else:
            sel_mod_single = st.selectbox(
                "Select Module",
                _db_mod_list,
                format_func=lambda m: (
                    f"🔴  {m}  ({int(_db_mods.loc[_db_mods['MODULE']==m,'FLUCTUATING_SQLS'].values[0])} fluctuating)"
                    if int(_db_mods.loc[_db_mods["MODULE"]==m,"FLUCTUATING_SQLS"].values[0]) > 0
                    else f"🟢  {m}  (all stable)"
                ),
                key="mod_single_sel",
            )
            _drill = summary_df[
                (summary_df["DATABASE"] == sel_mod_db) &
                (summary_df["MODULE"]   == sel_mod_single)
            ].copy()

            if _drill.empty:
                st.info(f"No SQLs found for **{sel_mod_single}** on **{sel_mod_db}**.")
            else:
                _fc = int((_drill["PLAN_COUNT"] > 1).sum())
                _sc = int((_drill["PLAN_COUNT"] == 1).sum())
                _bc, _bt = ("#f0fdf4","#22c55e") if _fc == 0 else ("#fff1f0","#ef4444")
                st.markdown(
                    f"<div style='background:{_bc};border:1.5px solid {_bt};border-radius:10px;"
                    f"padding:12px 18px;margin-bottom:14px;display:flex;align-items:center;gap:12px;'>"
                    f"<span style='font-size:1.5rem;'>{'✅' if _fc==0 else '⚠️'}</span>"
                    f"<div><b style='color:{_bt};font-size:1rem;'>{sel_mod_single}</b>"
                    f" on <b style='color:#2e55c8;'>{sel_mod_db}</b>"
                    f"<div style='color:#5a7ab0;font-size:0.85rem;margin-top:2px;'>"
                    f"{'All SQLs stable' if _fc==0 else f'{_fc} SQL(s) have plan fluctuations — investigate these'}"
                    f"</div></div></div>",
                    unsafe_allow_html=True,
                )

                sd1, sd2, sd3 = st.columns(3)
                sd1.metric("Total SQLs in Module", len(_drill))
                sd2.metric("🔴 Fluctuating",        _fc)
                sd3.metric("🟢 Stable",              _sc)

                _drill.insert(0, "Status", _drill["PLAN_COUNT"].apply(
                    lambda x: "🔴 Fluctuating" if x > 1 else "🟢 Stable"))
                _show = ["Status","SQL_ID","ACTION","PLAN_COUNT","TOTAL_EXECUTIONS","LAST_ACTIVE_TIME"]
                _show = [c for c in _show if c in _drill.columns]

                def _dr_single(row):
                    bg = "#fff1f0" if row["Status"] == "🔴 Fluctuating" else "#f0fdf4"
                    tc = "#dc2626" if row["Status"] == "🔴 Fluctuating" else "#16a34a"
                    return [f"background-color:{bg};color:{tc}"] + [f"background-color:{bg};color:#1e2a3a"] * (len(row)-1)

                st.dataframe(
                    _drill[_show].style.apply(_dr_single, axis=1)
                    .format({"TOTAL_EXECUTIONS":"{:,.0f}","PLAN_COUNT":"{:,.0f}"}),
                    use_container_width=True, hide_index=True,
                )

                if _fc > 0:
                    _fl = _drill[_drill["Status"] == "🔴 Fluctuating"]
                    fig_fl2 = px.bar(
                        _fl.sort_values("PLAN_COUNT", ascending=False),
                        x="SQL_ID", y="PLAN_COUNT", text="PLAN_COUNT",
                        color_discrete_sequence=["#f87171"],
                        title=f"Fluctuating SQLs — {sel_mod_single} on {sel_mod_db}",
                        labels={"PLAN_COUNT": "# Plans", "SQL_ID": "SQL ID"},
                    )
                    fig_fl2.update_layout(
                        paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                        font_color="#1e2a3a", title_font_color="#f87171",
                        xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb", tickangle=-30),
                        yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                        margin=dict(t=40, b=60, l=10, r=10),
                        showlegend=False,
                    )
                    fig_fl2.update_traces(textposition="outside", textfont_color="#1e2a3a")
                    st.plotly_chart(fig_fl2, use_container_width=True)

                st.download_button(
                    f"⬇️ Export {sel_mod_single} — {sel_mod_db} (CSV)",
                    data=_drill.drop(columns=["Status"]).to_csv(index=False),
                    file_name=f"module_{sel_mod_single.replace(' ','_')}_{sel_mod_db}.csv",
                    mime="text/csv", use_container_width=True,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # MODE 2 — Compare Two Databases
    # ══════════════════════════════════════════════════════════════════════════
    else:
        st.markdown(
            "<div style='background:#eef4ff;border-left:4px solid #4f6ef7;border-radius:8px;"
            "padding:12px 16px;margin-bottom:16px;color:#3a4f6e;font-size:0.9rem;'>"
            "⚖️ <b>Compare mode:</b> Select two databases and a module to see their "
            "plan stability side by side. Great for spotting if the same SQL behaves "
            "differently across environments (e.g. PROD vs UAT)."
            "</div>",
            unsafe_allow_html=True,
        )

        cmp_c1, cmp_c2, cmp_c3 = st.columns([2, 2, 2])
        with cmp_c1:
            cmp_db1 = st.selectbox(
                "🗄️ Database A",
                _all_dbs,
                key="cmp_db1",
                help="First database to compare.",
            )
        with cmp_c2:
            _db2_options = [d for d in _all_dbs if d != cmp_db1] or _all_dbs
            cmp_db2 = st.selectbox(
                "🗄️ Database B",
                _db2_options,
                key="cmp_db2",
                help="Second database to compare against.",
            )
        with cmp_c3:
            # Modules that exist in BOTH databases
            _mods_db1 = set(summary_df[summary_df["DATABASE"] == cmp_db1]["MODULE"].unique())
            _mods_db2 = set(summary_df[summary_df["DATABASE"] == cmp_db2]["MODULE"].unique())
            _common_mods = sorted(_mods_db1 & _mods_db2)
            _only_db1    = sorted(_mods_db1 - _mods_db2)
            _only_db2    = sorted(_mods_db2 - _mods_db1)
            _mod_opts    = (
                [f"[Both] {m}" for m in _common_mods] +
                [f"[{cmp_db1} only] {m}" for m in _only_db1] +
                [f"[{cmp_db2} only] {m}" for m in _only_db2]
            )
            if not _mod_opts:
                st.warning("No modules found in either database.")
                st.stop()
            cmp_mod_sel = st.selectbox(
                "📦 Module to compare",
                _mod_opts,
                key="cmp_mod",
                help="Modules available in both databases are listed first.",
            )
            # Strip the prefix tag to get actual module name
            cmp_mod = cmp_mod_sel.split("] ", 1)[-1] if "] " in cmp_mod_sel else cmp_mod_sel

        st.divider()

        # ── Comparison headline banner ─────────────────────────────────────────
        st.markdown(
            f"<div style='text-align:center;padding:10px 0 4px 0;'>"
            f"<span style='font-size:1.2rem;font-weight:700;color:#2e55c8;'>{cmp_db1}</span>"
            f"<span style='font-size:1.1rem;color:#5a7ab0;'> vs </span>"
            f"<span style='font-size:1.2rem;font-weight:700;color:#7c3aed;'>{cmp_db2}</span>"
            f"<span style='font-size:0.95rem;color:#5a7ab0;'> — Module: <b>{cmp_mod}</b></span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # Gather data for each DB
        def _mod_stats(db, mod):
            d = summary_df[(summary_df["DATABASE"] == db) & (summary_df["MODULE"] == mod)]
            if d.empty:
                return None, 0, 0, 0
            fc = int((d["PLAN_COUNT"] > 1).sum())
            sc = int((d["PLAN_COUNT"] == 1).sum())
            hp = round(sc / len(d) * 100, 1)
            return d, fc, sc, hp

        _d1, _fc1, _sc1, _hp1 = _mod_stats(cmp_db1, cmp_mod)
        _d2, _fc2, _sc2, _hp2 = _mod_stats(cmp_db2, cmp_mod)

        # ── KPI comparison row ─────────────────────────────────────────────────
        kc1, kc2, kc3, kc4, kc5, kc6 = st.columns(6)
        kc1.metric(f"[{cmp_db1}] Total SQLs",   len(_d1) if _d1 is not None else 0)
        kc2.metric(f"[{cmp_db1}] 🔴 Fluctuating", _fc1,
                   delta=f"{_fc1/len(_d1)*100:.0f}%" if _d1 is not None and len(_d1) else "0%",
                   delta_color="inverse")
        kc3.metric(f"[{cmp_db1}] Health %",      f"{_hp1:.1f}%",
                   delta_color="off")
        kc4.metric(f"[{cmp_db2}] Total SQLs",   len(_d2) if _d2 is not None else 0)
        kc5.metric(f"[{cmp_db2}] 🔴 Fluctuating", _fc2,
                   delta=f"{_fc2/len(_d2)*100:.0f}%" if _d2 is not None and len(_d2) else "0%",
                   delta_color="inverse")
        kc6.metric(f"[{cmp_db2}] Health %",      f"{_hp2:.1f}%",
                   delta_color="off")

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Side-by-side bar chart ─────────────────────────────────────────────
        _cmp_chart_df = pd.DataFrame({
            "Database": [cmp_db1, cmp_db1, cmp_db2, cmp_db2],
            "Category": ["🔴 Fluctuating", "🟢 Stable", "🔴 Fluctuating", "🟢 Stable"],
            "Count":    [_fc1, _sc1, _fc2, _sc2],
        })
        fig_cmp = px.bar(
            _cmp_chart_df, x="Database", y="Count",
            color="Category", barmode="group", text="Count",
            color_discrete_map={"🔴 Fluctuating": "#f87171", "🟢 Stable": "#4ade80"},
            title=f"Module '{cmp_mod}' — Plan Stability Comparison",
            labels={"Count": "SQL Count"},
        )
        fig_cmp.update_layout(
            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
            font_color="#1e2a3a", title_font_color="#2e55c8",
            xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
            yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
            legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
            margin=dict(t=50, b=20, l=10, r=10),
        )
        fig_cmp.update_traces(textposition="outside", textfont_color="#1e2a3a")
        st.plotly_chart(fig_cmp, use_container_width=True)

        # ── Side-by-side SQL tables ────────────────────────────────────────────
        st.markdown('<div class="section-header">📋 SQL-Level Comparison</div>',
                    unsafe_allow_html=True)
        st.caption(
            "🔴 = SQL changed plans (problem).  🟢 = stable.  "
            "If the same SQL_ID is 🔴 in one DB and 🟢 in another, "
            "that's a strong signal the issue is environment-specific."
        )

        tbl_left, tbl_right = st.columns(2)

        def _render_mod_table(col, db, data, fc, sc):
            icon = "🔴" if fc > 0 else "🟢"
            col.markdown(
                f"<div style='background:{'#fff1f0' if fc>0 else '#f0fdf4'};"
                f"border-left:4px solid {'#ef4444' if fc>0 else '#22c55e'};"
                f"border-radius:8px;padding:10px 14px;margin-bottom:10px;'>"
                f"<b style='color:{'#dc2626' if fc>0 else '#16a34a'};font-size:1rem;'>"
                f"{icon} {db}</b>"
                f"<span style='color:#5a7ab0;font-size:0.85rem;margin-left:8px;'>"
                f"{fc} fluctuating / {sc} stable</span></div>",
                unsafe_allow_html=True,
            )
            if data is None or data.empty:
                col.info(f"Module **{cmp_mod}** not found in **{db}**.")
            else:
                _d = data.copy()
                _d.insert(0, "Status", _d["PLAN_COUNT"].apply(
                    lambda x: "🔴" if x > 1 else "🟢"))
                _cols = [c for c in ["Status","SQL_ID","PLAN_COUNT","TOTAL_EXECUTIONS",
                                      "LAST_ACTIVE_TIME"] if c in _d.columns]

                def _sty(row):
                    bg = "#fff1f0" if row["Status"] == "🔴" else "#f0fdf4"
                    return [f"background-color:{bg}"] * len(row)

                col.dataframe(
                    _d[_cols].style.apply(_sty, axis=1)
                    .format({"TOTAL_EXECUTIONS":"{:,.0f}","PLAN_COUNT":"{:,.0f}"}),
                    use_container_width=True, hide_index=True,
                )
                col.download_button(
                    f"⬇️ Export {db} — {cmp_mod}",
                    data=_d.drop(columns=["Status"]).to_csv(index=False),
                    file_name=f"module_{cmp_mod.replace(' ','_')}_{db}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key=f"dl_cmp_{db}",
                )

        _render_mod_table(tbl_left,  cmp_db1, _d1, _fc1, _sc1)
        _render_mod_table(tbl_right, cmp_db2, _d2, _fc2, _sc2)

        # ── SQL IDs in common between both DBs ─────────────────────────────────
        if _d1 is not None and _d2 is not None:
            _ids1 = set(_d1["SQL_ID"])
            _ids2 = set(_d2["SQL_ID"])
            _common_ids = _ids1 & _ids2
            if _common_ids:
                st.divider()
                st.markdown('<div class="section-header">🔗 SQL IDs Present in Both Databases</div>',
                            unsafe_allow_html=True)
                st.caption(
                    "These SQL IDs exist in both databases for this module. "
                    "Rows highlighted 🔴 in one DB and 🟢 in the other indicate "
                    "an environment-specific plan problem."
                )
                _common_merge = pd.merge(
                    _d1[["SQL_ID","PLAN_COUNT","TOTAL_EXECUTIONS"]].rename(
                        columns={"PLAN_COUNT":f"Plans ({cmp_db1})","TOTAL_EXECUTIONS":f"Execs ({cmp_db1})"}),
                    _d2[["SQL_ID","PLAN_COUNT","TOTAL_EXECUTIONS"]].rename(
                        columns={"PLAN_COUNT":f"Plans ({cmp_db2})","TOTAL_EXECUTIONS":f"Execs ({cmp_db2})"}),
                    on="SQL_ID", how="inner",
                )
                _pc1_col = f"Plans ({cmp_db1})"
                _pc2_col = f"Plans ({cmp_db2})"
                _common_merge["Status"] = _common_merge.apply(
                    lambda r: (
                        "🔴 Both fluctuating"     if r[_pc1_col] > 1 and r[_pc2_col] > 1
                        else f"⚠️ Only {cmp_db1}"  if r[_pc1_col] > 1
                        else f"⚠️ Only {cmp_db2}"  if r[_pc2_col] > 1
                        else "🟢 Both stable"
                    ), axis=1,
                )
                _common_merge = _common_merge[["Status","SQL_ID",_pc1_col,_pc2_col,
                                               f"Execs ({cmp_db1})",f"Execs ({cmp_db2})"]]

                def _cmp_style(row):
                    if "Both fluctuating" in row["Status"]:
                        bg = "#fff1f0"
                    elif "Only" in row["Status"]:
                        bg = "#fffbeb"
                    else:
                        bg = "#f0fdf4"
                    return [f"background-color:{bg}"] * len(row)

                st.dataframe(
                    _common_merge.style.apply(_cmp_style, axis=1)
                    .format({_pc1_col:"{:,.0f}", _pc2_col:"{:,.0f}",
                             f"Execs ({cmp_db1})":"{:,.0f}", f"Execs ({cmp_db2})":"{:,.0f}"}),
                    use_container_width=True, hide_index=True,
                )
                st.download_button(
                    "⬇️ Export comparison table (CSV)",
                    data=_common_merge.to_csv(index=False),
                    file_name=f"compare_{cmp_mod.replace(' ','_')}_{cmp_db1}_vs_{cmp_db2}.csv",
                    mime="text/csv", use_container_width=True, key="dl_cmp_common",
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Plan Analysis
# ══════════════════════════════════════════════════════════════════════════════
with tab_plan:
    st.markdown('<div class="section-header">⚡ Plan Comparison — Avg Execution Time</div>',
                unsafe_allow_html=True)
    st.info(
        "⚡ **What this tab does:** Pick any SQL ID to see all the execution plans Oracle has used "
        "for it. The **🟢 green bar** is the fastest plan (lowest avg elapsed time). "
        "**🔴 Red bars** are slower plans — if Oracle is currently using a red plan, "
        "that's your performance problem. Use the SQL Tuning Advisor tab to fix it.",
    )

    pa_col1, pa_col2 = st.columns([2, 1])
    with pa_col1:
        sel_pa_sql_dd = st.selectbox(
            "Select SQL ID from loaded data",
            summary_df["SQL_ID"].drop_duplicates().tolist(),
            format_func=lambda x: (
                f"🔴 {x}  ({int(summary_df.loc[summary_df['SQL_ID']==x,'PLAN_COUNT'].max())} plans)"
                if int(summary_df.loc[summary_df["SQL_ID"]==x,"PLAN_COUNT"].max()) > 1
                else f"🟢 {x}  (1 plan)"
            ),
            help="🔴 = SQL changed execution plans. 🟢 = stable. Fluctuating SQLs are listed first.",
            key="pa_sql",
        )
        pa_manual = st.text_input(
            "Or type a SQL ID manually",
            value="",
            placeholder="e.g. 3g8fqh2v9fk7u",
            help="Type a SQL ID that may not be in the loaded data. This overrides the dropdown above.",
            key="pa_sql_manual",
        )
        sel_pa_sql = pa_manual.strip() if pa_manual.strip() else sel_pa_sql_dd
        if pa_manual.strip():
            st.caption(f"📝 Using manually entered SQL ID: **{sel_pa_sql}**")
    with pa_col2:
        all_db_names = [c["name"] for c in active_cfgs]
        has_sql_dbs  = summary_df[summary_df["SQL_ID"] == sel_pa_sql]["DATABASE"].unique().tolist()
        other_dbs    = [d for d in all_db_names if d not in has_sql_dbs]
        ordered_dbs  = has_sql_dbs + other_dbs
        sel_pa_db = st.selectbox(
            "Database",
            ordered_dbs,
            format_func=lambda d: (
                f"✅ {d}  — SQL found here"
                if d in has_sql_dbs
                else f"⬜ {d}  — SQL not in loaded data"
            ),
            help="✅ = SQL was found in this database during the last load.  "
                 "⬜ = SQL not in loaded data for this DB (will still try to query live).",
            key="pa_db",
        )
        if sel_pa_db in has_sql_dbs:
            st.success(f"✅ SQL **{sel_pa_sql}** was found in **{sel_pa_db}**.", icon=None)
        else:
            st.warning(
                f"⬜ SQL **{sel_pa_sql}** was **not found** in the loaded data for "
                f"**{sel_pa_db}**. Plan data will be queried live from V$SQL — "
                "results may be empty if the SQL is not in the cursor cache.",
            )

    pa_cfg = next((c for c in active_cfgs if c["name"]==sel_pa_db), None)

    if pa_cfg:
        try:
            sql_text = scalar_one_db(pa_cfg, SQL_TEXT_QUERY, {"sql_id": sel_pa_sql})
            if sql_text:
                with st.expander("📄 SQL Text"):
                    st.code(str(sql_text), language="sql")
        except Exception:
            pass

        try:
            plans_df = query_one_db(pa_cfg, SQL_PLANS_AVG_QUERY, {"sql_id": sel_pa_sql})
            plans_df.drop(columns=["DATABASE"], errors="ignore", inplace=True)
        except Exception as exc:
            handle_ora_error(exc)
            plans_df = pd.DataFrame()

        if plans_df.empty:
            st.info("No plan data found for this SQL_ID.")
        else:
            min_el = plans_df["AVG_ELAPSED_SEC"].min()
            max_el = plans_df["AVG_ELAPSED_SEC"].max()
            plans_df["IS_BEST"] = plans_df["AVG_ELAPSED_SEC"] == min_el

            ch1, ch2 = st.columns([3, 2])
            with ch1:
                fig_pl = go.Figure()
                for _, row in plans_df.iterrows():
                    col = "#4ade80" if row["IS_BEST"] else "#f87171"
                    fig_pl.add_trace(go.Bar(
                        x=[str(row["PLAN_HASH_VALUE"])], y=[row["AVG_ELAPSED_SEC"]],
                        marker_color=col, name=str(row["PLAN_HASH_VALUE"]),
                        text=[f"{row['AVG_ELAPSED_SEC']:.4f}s"], textposition="outside",
                        textfont=dict(color="#1e2a3a"),
                    ))
                fig_pl.update_layout(
                    title="Avg Elapsed Time per Plan (seconds)",
                    paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                    font_color="#1e2a3a", title_font_color="#2e55c8",
                    xaxis=dict(title="Plan Hash Value",tickfont=dict(color="#5a7ab0"),gridcolor="#dce6fb"),
                    yaxis=dict(title="Avg Elapsed (sec)",tickfont=dict(color="#5a7ab0"),gridcolor="#dce6fb"),
                    showlegend=False, margin=dict(t=40,b=10,l=10,r=10),
                )
                st.plotly_chart(fig_pl, use_container_width=True)
                if len(plans_df)>1 and min_el and min_el>0:
                    st.markdown(f'<div style="text-align:center;"><span class="badge-red">⚠️ Slowest plan is {max_el/min_el:.1f}x slower</span></div>', unsafe_allow_html=True)

            with ch2:
                def _pr(row):
                    c = "#f0fdf4" if row["AVG_ELAPSED_SEC"]==min_el else "#fff1f0"
                    return [f"background-color:{c}"]*len(row)
                disp_cols = ["PLAN_HASH_VALUE","AVG_ELAPSED_SEC","AVG_CPU_SEC",
                             "AVG_BUFFER_GETS","AVG_DISK_READS","TOTAL_EXECUTIONS"]
                st.dataframe(
                    plans_df[disp_cols].style.apply(_pr,axis=1).format({
                        "AVG_ELAPSED_SEC":"{:.4f}","AVG_CPU_SEC":"{:.4f}",
                        "AVG_BUFFER_GETS":"{:,.0f}","AVG_DISK_READS":"{:,.0f}",
                        "TOTAL_EXECUTIONS":"{:,.0f}"}),
                    use_container_width=True, hide_index=True,
                )

            if len(plans_df)>1:
                with st.expander("📡 Radar Chart — Plan Characteristics"):
                    dims = ["AVG_ELAPSED_SEC","AVG_CPU_SEC","AVG_BUFFER_GETS","AVG_DISK_READS"]
                    radar = go.Figure()
                    norm = plans_df[dims].max()
                    colors = ["#4ade80","#f87171","#60a5fa","#facc15","#a78bfa"]
                    for i,(_,row) in enumerate(plans_df.iterrows()):
                        vals = [(row[d]/norm[d] if norm[d]>0 else 0) for d in dims]
                        vals.append(vals[0])
                        radar.add_trace(go.Scatterpolar(r=vals, theta=dims+[dims[0]],
                            fill="toself", name=str(row["PLAN_HASH_VALUE"]),
                            line_color=colors[i%len(colors)], opacity=0.7))
                    radar.update_layout(
                        polar=dict(bgcolor="#f0f4ff",
                            radialaxis=dict(visible=True,color="#5a7ab0",gridcolor="#dce6fb"),
                            angularaxis=dict(color="#5a7ab0",gridcolor="#dce6fb")),
                        paper_bgcolor="#ffffff", font_color="#1e2a3a",
                        legend=dict(font=dict(color="#1e2a3a")),
                        title="Normalized Plan Cost Comparison", title_font_color="#2e55c8",
                    )
                    st.plotly_chart(radar, use_container_width=True)

            with st.expander("🔬 Execution Plan Steps (what Oracle does inside to run this SQL)"):
                sel_hash = st.selectbox(
                    "Plan ID (Hash Value)",
                    plans_df["PLAN_HASH_VALUE"].tolist(),
                    help="Select which plan to inspect. Each plan ID represents a different "
                         "execution strategy Oracle used for this SQL.",
                    key="steps_hash",
                )
                try:
                    steps_df = query_one_db(pa_cfg, SQL_PLAN_STEPS_QUERY,
                        {"sql_id":sel_pa_sql,"plan_hash_value":sel_hash})
                    steps_df.drop(columns=["DATABASE"], errors="ignore", inplace=True)
                    st.dataframe(
                        steps_df.style.background_gradient(subset=["COST"],cmap="YlOrRd"),
                        use_container_width=True, hide_index=True)
                except Exception as exc:
                    handle_ora_error(exc)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — SQL Tuning Advisor
# ══════════════════════════════════════════════════════════════════════════════
with tab_advisor:
    st.markdown('<div class="section-header">🧠 SQL Tuning Advisor</div>',
                unsafe_allow_html=True)
    st.info(
        "🧠 **What this tab does:** Runs Oracle's built-in SQL Tuning Advisor on any SQL ID. "
        "It analyses the SQL and recommends fixes such as: creating a **SQL Profile** (pins "
        "the best plan), adding a missing **index**, refreshing **statistics**, or rewriting "
        "the SQL. Requires Oracle Tuning Pack license.",
    )

    adv1, adv2, adv3 = st.columns([2, 1, 1])
    with adv1:
        _adv_sqls = (
            summary_df.sort_values("PLAN_COUNT", ascending=False)["SQL_ID"]
            .drop_duplicates().tolist()
        )
        adv_sql_dd = st.selectbox(
            "Select SQL ID from loaded data",
            _adv_sqls,
            format_func=lambda x: (
                f"🔴 {x}  ({int(summary_df.loc[summary_df['SQL_ID']==x,'PLAN_COUNT'].max())} plans)"
                if int(summary_df.loc[summary_df["SQL_ID"]==x,"PLAN_COUNT"].max()) > 1
                else f"🟢 {x}  (stable)"
            ),
            help="🔴 = SQL changed execution plans — prioritise these. 🟢 = stable.",
            key="adv_sql",
        )
        adv_manual = st.text_input(
            "Or type a SQL ID manually",
            value="",
            placeholder="e.g. 3g8fqh2v9fk7u",
            help="Type a SQL ID that may not be in the loaded data. This overrides the dropdown above.",
            key="adv_sql_manual",
        )
        adv_sql_id = adv_manual.strip() if adv_manual.strip() else adv_sql_dd
        if adv_manual.strip():
            st.caption(f"📝 Using manually entered SQL ID: **{adv_sql_id}**")
    with adv2:
        adv_has_dbs   = summary_df[summary_df["SQL_ID"] == adv_sql_id]["DATABASE"].unique().tolist()
        adv_other_dbs = [c["name"] for c in active_cfgs if c["name"] not in adv_has_dbs]
        adv_db_name = st.selectbox(
            "Database",
            adv_has_dbs + adv_other_dbs,
            format_func=lambda d: (
                f"✅ {d}  — SQL found here"
                if d in adv_has_dbs
                else f"⬜ {d}  — SQL not in loaded data"
            ),
            help="✅ = SQL was found in this database during the last load.  "
                 "⬜ = SQL not found in loaded data — advisor may fail if SQL is not in cursor cache.",
            key="adv_db",
        )
        if adv_db_name in adv_has_dbs:
            st.success(f"✅ SQL found in **{adv_db_name}**", icon=None)
        else:
            st.warning(f"⬜ SQL not in loaded data for **{adv_db_name}** — Advisor may return no results.")
    with adv3:
        adv_time = st.slider(
            "Analysis time limit (seconds)", 30, 600, 60, 30,
            help="How long Oracle will spend analysing the SQL. "
                 "Longer = more thorough analysis. "
                 "60 seconds is suitable for most cases. "
                 "Increase to 300s+ for very complex queries.",
        )

    adv_cfg = next((c for c in active_cfgs if c["name"]==adv_db_name), None)

    if adv_cfg:
        try:
            adv_sql_text = scalar_one_db(adv_cfg, SQL_TEXT_QUERY, {"sql_id": adv_sql_id})
            if adv_sql_text:
                with st.expander("📄 SQL Text to tune"):
                    st.code(str(adv_sql_text), language="sql")
        except Exception:
            pass

    run_adv = st.button("🧠 Run SQL Tuning Advisor", type="primary")

    if run_adv and adv_cfg:
        task_hint = f"DASH_{adv_sql_id}_{int(time.time())}"
        pb = st.progress(0, text="Initialising…")
        with st.spinner(f"Tuning `{adv_sql_id}` on `{adv_db_name}` (up to {adv_time}s)…"):
            try:
                with get_connection(adv_cfg) as conn:
                    with conn.cursor() as cur:
                        pb.progress(20, text="Creating tuning task…")
                        tv = cur.var(str)
                        cur.execute("""
                            BEGIN
                              :task_name := DBMS_SQLTUNE.CREATE_TUNING_TASK(
                                  sql_id      => :sql_id,
                                  scope       => DBMS_SQLTUNE.SCOPE_COMPREHENSIVE,
                                  time_limit  => :time_limit,
                                  task_name   => :task_name_hint,
                                  description => 'SQL Plan Dashboard tuning task'
                              );
                            END;
                        """, {"task_name":tv,"sql_id":adv_sql_id,
                              "time_limit":adv_time,"task_name_hint":task_hint})
                        tname = tv.getvalue()
                        pb.progress(40, text=f"Executing '{tname}'…")
                        cur.execute("BEGIN DBMS_SQLTUNE.EXECUTE_TUNING_TASK(task_name=>:t); END;",{"t":tname})
                        conn.commit()
                        pb.progress(80, text="Fetching report…")
                        cur.execute("SELECT DBMS_SQLTUNE.REPORT_TUNING_TASK(:t) FROM DUAL",{"t":tname})
                        row = cur.fetchone()
                        val = row[0] if row else None
                        report_text = (val.read() if hasattr(val,"read") else str(val)) if val else "No report."
                        cur.execute("BEGIN DBMS_SQLTUNE.DROP_TUNING_TASK(task_name=>:t); END;",{"t":tname})
                        conn.commit()
                        pb.progress(100, text="Done!")
            except Exception as exc:
                pb.empty()
                handle_ora_error(exc, adv_cfg["user"])
                st.stop()

        st.success(f"✅ Advisor completed for `{adv_sql_id}` on **{adv_db_name}**")
        _render_advisor_report(report_text, adv_sql_id)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — SQL Execution History (AWR-based timeline)
# ══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.markdown('<div class="section-header">📈 SQL Execution History (AWR)</div>',
                unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#5a7ab0;'>Track how a SQL's performance and execution plan "
        "changed over time using DBA_HIST_SQLSTAT snapshots.</p>",
        unsafe_allow_html=True,
    )

    # ── Controls ──────────────────────────────────────────────────────────────
    hc1, hc2, hc3 = st.columns([2, 2, 1])
    with hc1:
        # Build SQL_ID list — fluctuating first so they're easiest to find
        _hist_sqls = (
            summary_df.sort_values("PLAN_COUNT", ascending=False)["SQL_ID"]
            .drop_duplicates().tolist()
        )
        hist_sql_id_sel = st.selectbox(
            "Select SQL ID from loaded data",
            options=_hist_sqls,
            format_func=lambda x: (
                f"🔴 {x}  ({int(summary_df.loc[summary_df['SQL_ID']==x,'PLAN_COUNT'].max())} plans)"
                if int(summary_df.loc[summary_df["SQL_ID"]==x,"PLAN_COUNT"].max()) > 1
                else f"🟢 {x}  (stable)"
            ),
            help="🔴 = SQL changed execution plans (investigate these first). 🟢 = stable.",
            key="hist_sql_sel",
        )
        hist_sql_override = st.text_input(
            "Or type a SQL ID manually",
            value="",
            placeholder="e.g. 3g8fqh2v9fk7u",
            help="Type a SQL ID that may not be in the loaded data. This overrides the dropdown above.",
            key="hist_sql_id",
        )
        hist_sql_id = hist_sql_override.strip() if hist_sql_override.strip() else hist_sql_id_sel
        if hist_sql_override.strip():
            st.caption(f"📝 Using manually entered SQL ID: **{hist_sql_id}**")

    with hc2:
        _hist_has_dbs  = summary_df[summary_df["SQL_ID"] == hist_sql_id]["DATABASE"].unique().tolist()
        _hist_other    = [c["name"] for c in active_cfgs if c["name"] not in _hist_has_dbs]
        hist_db = st.selectbox(
            "Database",
            _hist_has_dbs + _hist_other,
            format_func=lambda d: (
                f"✅ {d}  — SQL found here"
                if d in _hist_has_dbs
                else f"⬜ {d}  — SQL not in loaded data"
            ),
            help="✅ = SQL was found in this database during the last load (AWR history likely available).  "
                 "⬜ = SQL not found in loaded data — AWR history may still exist, but SQL is not in current cursor cache.",
            key="hist_db",
        )
        if hist_db in _hist_has_dbs:
            st.success(f"✅ SQL found in **{hist_db}** — AWR history available", icon=None)
        else:
            st.caption(f"⬜ SQL not in current loaded data for **{hist_db}** — AWR history may still exist.")
    with hc3:
        hist_snap_limit = st.number_input(
            "Snapshots to load",
            min_value=10, max_value=500, value=100, step=10,
            help="How many recent AWR snapshots to show in the range picker. Each snapshot is ~1 hour.",
            key="hist_snap_limit",
        )

    hist_cfg = next((c for c in active_cfgs if c["name"] == hist_db), None)

    if not hist_sql_id.strip():
        st.info("Select a SQL ID above to load its execution history.")
        st.stop()

    # ── Load snapshots for range picker ───────────────────────────────────────
    if hist_cfg:
        snap_cache_key = f"awr_snaps_{hist_db}"
        if snap_cache_key not in st.session_state or st.button("🔄 Reload Snapshots", key="hist_reload"):
            with st.spinner("Loading AWR snapshot list…"):
                st.session_state[snap_cache_key] = fetch_awr_snapshots(hist_cfg, int(hist_snap_limit))

        snaps_df = st.session_state.get(snap_cache_key, pd.DataFrame())

        if snaps_df.empty or "error" in snaps_df.columns:
            err_msg = snaps_df["error"].iloc[0] if "error" in snaps_df.columns else "No snapshots found."
            st.error(f"Could not load snapshots: {err_msg}")
            st.code(
                f"GRANT SELECT ON DBA_HIST_SNAPSHOT TO {hist_cfg['user']};\n"
                f"GRANT SELECT ON DBA_HIST_SQLSTAT   TO {hist_cfg['user']};\n"
                f"GRANT SELECT ON DBA_HIST_SQL_PLAN  TO {hist_cfg['user']};",
                language="sql",
            )
        else:
            snap_ids   = snaps_df["SNAP_ID"].tolist()
            snap_labels = [
                f"{row['SNAP_ID']}  ({row['BEGIN_TIME']} → {row['END_TIME']})"
                for _, row in snaps_df.iterrows()
            ]
            snap_id_to_label = dict(zip(snap_ids, snap_labels))

            sr1, sr2 = st.columns(2)
            with sr1:
                begin_snap_sel = st.selectbox(
                    "Begin Snapshot",
                    options=snap_ids[::-1],
                    format_func=lambda x: snap_id_to_label.get(x, str(x)),
                    index=max(0, len(snap_ids) - 24),
                    key="hist_begin_snap",
                )
            with sr2:
                end_snap_sel = st.selectbox(
                    "End Snapshot",
                    options=snap_ids[::-1],
                    format_func=lambda x: snap_id_to_label.get(x, str(x)),
                    index=0,
                    key="hist_end_snap",
                )

            load_hist = st.button("📈 Load Execution History", type="primary", key="load_hist")

            if load_hist or f"hist_data_{hist_sql_id}_{hist_db}" in st.session_state:
                hist_cache_key = f"hist_data_{hist_sql_id}_{hist_db}"
                if load_hist:
                    with st.spinner("Fetching execution history from AWR…"):
                        try:
                            raw = query_one_db(
                                hist_cfg, SQL_HISTORY_QUERY,
                                {"sql_id": hist_sql_id.strip(),
                                 "begin_snap_id": int(begin_snap_sel),
                                 "end_snap_id":   int(end_snap_sel)},
                            )
                            raw.drop(columns=["DATABASE"], errors="ignore", inplace=True)
                            raw = _numeric_cols_to_float(raw)
                            st.session_state[hist_cache_key] = raw
                        except Exception as exc:
                            handle_ora_error(exc, hist_cfg["user"])
                            st.stop()

                hist_df = st.session_state.get(hist_cache_key, pd.DataFrame())

                if hist_df is None or hist_df.empty:
                    st.warning(
                        f"No AWR history found for SQL_ID **{hist_sql_id}** in the selected snapshot range. "
                        "The SQL may not have executed during this window, or it may not be captured in AWR."
                    )
                else:
                    unique_plans = hist_df["PLAN_HASH_VALUE"].unique().tolist()
                    plan_colors  = ["#4ade80", "#f87171", "#60a5fa", "#facc15",
                                    "#a78bfa", "#fb923c", "#34d399", "#f472b6"]
                    color_map = {str(p): plan_colors[i % len(plan_colors)]
                                 for i, p in enumerate(unique_plans)}

                    # ── KPI row ───────────────────────────────────────────────
                    hk1, hk2, hk3, hk4, hk5 = st.columns(5)
                    hk1.metric("Snapshots with data",    len(hist_df))
                    hk2.metric("Distinct Plans",          len(unique_plans))
                    hk3.metric("Total Executions",
                               f"{int(hist_df['EXECUTIONS'].sum()):,}")
                    hk4.metric("Best Avg Elapsed (s)",
                               f"{hist_df['AVG_ELAPSED_SEC'].min():.4f}")
                    hk5.metric("Worst Avg Elapsed (s)",
                               f"{hist_df['AVG_ELAPSED_SEC'].max():.4f}")

                    st.markdown("<br>", unsafe_allow_html=True)

                    # ── Timeline: Avg Elapsed coloured by plan ────────────────
                    fig_hist = go.Figure()
                    for plan_hash in unique_plans:
                        ph_str = str(plan_hash)
                        subset = hist_df[hist_df["PLAN_HASH_VALUE"] == plan_hash]
                        fig_hist.add_trace(go.Scatter(
                            x=subset["SNAP_TIME"],
                            y=subset["AVG_ELAPSED_SEC"],
                            mode="lines+markers",
                            name=f"Plan {ph_str}",
                            line=dict(color=color_map[ph_str], width=2),
                            marker=dict(size=6, color=color_map[ph_str]),
                            hovertemplate=(
                                "<b>%{x}</b><br>"
                                f"Plan: {ph_str}<br>"
                                "Avg Elapsed: %{y:.4f}s<extra></extra>"
                            ),
                        ))

                    # Mark plan-change points with separate shape + annotation
                    # (add_vline annotation fails on string x-axes in newer Plotly versions)
                    hist_df_sorted = hist_df.sort_values("SNAP_ID").reset_index(drop=True)
                    for i in range(1, len(hist_df_sorted)):
                        if hist_df_sorted.loc[i, "PLAN_HASH_VALUE"] != hist_df_sorted.loc[i-1, "PLAN_HASH_VALUE"]:
                            _xval = hist_df_sorted.loc[i, "SNAP_TIME"]
                            fig_hist.add_shape(
                                type="line",
                                x0=_xval, x1=_xval, y0=0, y1=1,
                                xref="x", yref="paper",
                                line=dict(color="#f59e0b", width=1.5, dash="dash"),
                            )
                            fig_hist.add_annotation(
                                x=_xval, y=1,
                                xref="x", yref="paper",
                                text="Plan Change",
                                showarrow=False,
                                font=dict(color="#f59e0b", size=10),
                                yanchor="bottom",
                                bgcolor="rgba(255,255,255,0.7)",
                                bordercolor="#f59e0b",
                                borderwidth=1,
                            )

                    fig_hist.update_layout(
                        title=f"Avg Elapsed Time Over Time — {hist_sql_id}",
                        paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                        font_color="#1e2a3a", title_font_color="#2e55c8",
                        xaxis=dict(title="Snapshot Time", tickfont=dict(color="#5a7ab0"),
                                   gridcolor="#dce6fb", tickangle=-30),
                        yaxis=dict(title="Avg Elapsed (sec)", tickfont=dict(color="#5a7ab0"),
                                   gridcolor="#dce6fb"),
                        legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
                        margin=dict(t=50, b=80, l=10, r=10),
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig_hist, use_container_width=True)

                    # ── Secondary metrics timeline ────────────────────────────
                    with st.expander("📊 Buffer Gets & Disk Reads Timeline"):
                        fig_io = go.Figure()
                        fig_io.add_trace(go.Scatter(
                            x=hist_df_sorted["SNAP_TIME"], y=hist_df_sorted["AVG_BUFFER_GETS"],
                            mode="lines+markers", name="Avg Buffer Gets",
                            line=dict(color="#60a5fa", width=2),
                        ))
                        fig_io.add_trace(go.Scatter(
                            x=hist_df_sorted["SNAP_TIME"], y=hist_df_sorted["AVG_DISK_READS"],
                            mode="lines+markers", name="Avg Disk Reads",
                            line=dict(color="#fb923c", width=2),
                        ))
                        fig_io.update_layout(
                            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                            font_color="#1e2a3a", title_font_color="#2e55c8",
                            xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb", tickangle=-30),
                            yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                            legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
                            margin=dict(t=30, b=60, l=10, r=10),
                            hovermode="x unified",
                        )
                        st.plotly_chart(fig_io, use_container_width=True)

                    # ── Executions per snapshot ───────────────────────────────
                    with st.expander("⚡ Executions per Snapshot"):
                        fig_exec = px.bar(
                            hist_df_sorted, x="SNAP_TIME", y="EXECUTIONS",
                            color=hist_df_sorted["PLAN_HASH_VALUE"].astype(str),
                            color_discrete_map=color_map,
                            labels={"EXECUTIONS": "Executions", "SNAP_TIME": "Snapshot",
                                    "color": "Plan Hash"},
                            title="Executions per AWR Snapshot",
                        )
                        fig_exec.update_layout(
                            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                            font_color="#1e2a3a", title_font_color="#2e55c8",
                            xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb", tickangle=-30),
                            yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                            legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
                            margin=dict(t=40, b=80, l=10, r=10),
                        )
                        st.plotly_chart(fig_exec, use_container_width=True)

                    # ── Raw history table ─────────────────────────────────────
                    with st.expander("📋 Raw Execution History Table"):
                        disp = hist_df_sorted[[
                            "SNAP_TIME", "PLAN_HASH_VALUE", "EXECUTIONS",
                            "AVG_ELAPSED_SEC", "AVG_CPU_SEC",
                            "AVG_BUFFER_GETS", "AVG_DISK_READS", "AVG_ROWS",
                        ]].copy()
                        disp["PLAN_HASH_VALUE"] = disp["PLAN_HASH_VALUE"].astype(str)
                        st.dataframe(
                            disp.style.background_gradient(subset=["AVG_ELAPSED_SEC"], cmap="RdYlGn_r"),
                            use_container_width=True, hide_index=True,
                        )
                        st.download_button(
                            "⬇️ Export History CSV",
                            data=disp.to_csv(index=False),
                            file_name=f"sql_history_{hist_sql_id}.csv",
                            mime="text/csv",
                        )

                    # ── Historical execution plan steps ───────────────────────
                    st.markdown('<div class="section-header">🔬 Historical Plan Steps</div>',
                                unsafe_allow_html=True)
                    hps1, hps2 = st.columns([2, 1])
                    with hps1:
                        sel_hist_plan = st.selectbox(
                            "Plan Hash Value",
                            [str(p) for p in unique_plans],
                            key="hist_plan_sel",
                        )
                    with hps2:
                        plan_src = st.radio(
                            "Source", ["AWR (DBA_HIST_SQL_PLAN)", "Cursor Cache (V$SQL_PLAN)"],
                            key="hist_plan_src",
                        )

                    try:
                        if "AWR" in plan_src:
                            steps_df = query_one_db(
                                hist_cfg, SQL_HIST_PLAN_STEPS_QUERY,
                                {"sql_id": hist_sql_id.strip(),
                                 "plan_hash_value": int(sel_hist_plan)},
                            )
                        else:
                            steps_df = query_one_db(
                                hist_cfg, SQL_PLAN_STEPS_QUERY,
                                {"sql_id": hist_sql_id.strip(),
                                 "plan_hash_value": int(sel_hist_plan)},
                            )
                        steps_df.drop(columns=["DATABASE"], errors="ignore", inplace=True)
                        steps_df = _numeric_cols_to_float(steps_df)

                        if steps_df.empty:
                            st.info(f"No plan steps found for plan hash {sel_hist_plan}. "
                                    "Try switching the source above.")
                        else:
                            st.dataframe(
                                steps_df.style.background_gradient(
                                    subset=["COST"], cmap="YlOrRd"
                                ),
                                use_container_width=True, hide_index=True,
                            )
                    except Exception as exc:
                        handle_ora_error(exc, hist_cfg["user"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — AWR Report
# ══════════════════════════════════════════════════════════════════════════════
with tab_awr:
    st.markdown('<div class="section-header">📋 AWR Report & Analysis</div>',
                unsafe_allow_html=True)
    st.info(
        "📋 **What this tab does:** AWR (Automatic Workload Repository) is Oracle's built-in "
        "performance history store — it takes a snapshot roughly every hour. "
        "This tab lets you analyse any time window. "
        "**Requires:** SELECT on DBA_HIST_* views (Oracle Diagnostics Pack license).",
    )

    # ── DB & snapshot selection ───────────────────────────────────────────────
    aw1, aw2 = st.columns([2, 1])
    with aw1:
        awr_db = st.selectbox(
            "Database", [c["name"] for c in active_cfgs], key="awr_db"
        )
    with aw2:
        awr_snap_limit = st.number_input(
            "Snapshots to load", min_value=10, max_value=500, value=100, step=10,
            key="awr_snap_limit",
        )

    awr_cfg = next((c for c in active_cfgs if c["name"] == awr_db), None)

    if awr_cfg:
        awr_snaps_key = f"awr_snaps_tab_{awr_db}"
        if awr_snaps_key not in st.session_state or st.button("🔄 Reload Snapshots", key="awr_reload"):
            with st.spinner("Loading AWR snapshot list…"):
                st.session_state[awr_snaps_key] = fetch_awr_snapshots(awr_cfg, int(awr_snap_limit))

        awr_snaps = st.session_state.get(awr_snaps_key, pd.DataFrame())

        if awr_snaps.empty or "error" in awr_snaps.columns:
            err_msg = awr_snaps["error"].iloc[0] if "error" in awr_snaps.columns else "No snapshots found."
            st.error(f"Could not load snapshots: {err_msg}")
            st.code(
                f"GRANT SELECT ON DBA_HIST_SNAPSHOT TO {awr_cfg['user']};\n"
                f"GRANT EXECUTE ON DBMS_WORKLOAD_REPOSITORY TO {awr_cfg['user']};",
                language="sql",
            )
        else:
            # Snapshot table preview
            with st.expander("📅 Available Snapshots", expanded=False):
                st.dataframe(awr_snaps, use_container_width=True, hide_index=True)

            snap_ids_awr   = awr_snaps["SNAP_ID"].tolist()
            snap_labels_awr = {
                row["SNAP_ID"]: f"{row['SNAP_ID']}  ({row['BEGIN_TIME']} → {row['END_TIME']})"
                for _, row in awr_snaps.iterrows()
            }

            asc1, asc2 = st.columns(2)
            with asc1:
                awr_begin = st.selectbox(
                    "Begin Snapshot",
                    options=sorted(snap_ids_awr),
                    format_func=lambda x: snap_labels_awr.get(x, str(x)),
                    index=max(0, len(snap_ids_awr) - 2),
                    key="awr_begin",
                )
            with asc2:
                awr_end = st.selectbox(
                    "End Snapshot",
                    options=sorted(snap_ids_awr),
                    format_func=lambda x: snap_labels_awr.get(x, str(x)),
                    index=len(snap_ids_awr) - 1,
                    key="awr_end",
                )

            if awr_begin >= awr_end:
                st.warning("Begin snapshot must be less than End snapshot.")
            else:
                st.markdown(
                    f"<p style='color:#5a7ab0;'>Selected range: "
                    f"<b style='color:#2e55c8;'>{snap_labels_awr.get(awr_begin, awr_begin)}</b>"
                    f" → <b style='color:#2e55c8;'>{snap_labels_awr.get(awr_end, awr_end)}</b></p>",
                    unsafe_allow_html=True,
                )

                st.markdown(
                    "<div style='background:#f0f4ff;border-radius:8px;padding:10px 16px;"
                    "margin-bottom:12px;color:#3a4f6e;font-size:0.88rem;'>"
                    "💡 <b>Recommended order:</b> &nbsp;"
                    "<b>Step 1</b> → Generate AWR Report &nbsp;|&nbsp; "
                    "<b>Step 2</b> → Analyze Top SQL &nbsp;|&nbsp; "
                    "<b>Step 3</b> → Wait Events &nbsp;|&nbsp; "
                    "<b>Step 4</b> → Analyze DB Performance (combines all findings)"
                    "</div>",
                    unsafe_allow_html=True,
                )
                ab1, ab2, ab3 = st.columns(3)
                run_awr_report  = ab1.button(
                    "📋 Step 1 — Generate AWR Report", type="primary", key="run_awr",
                    help="Generates Oracle's AWR report in both Text (.txt) and HTML (.html) formats.",
                )
                run_top_sql     = ab2.button(
                    "🔍 Step 2 — Analyze Top SQL", key="run_top_sql",
                    help="Shows the top 50 SQLs by total elapsed time in this period. "
                         "Use this to find which queries consumed the most database time.",
                )
                run_wait_events = ab3.button(
                    "⏳ Step 3 — Wait Events", key="run_wait",
                    help="Shows what the database was waiting for (I/O, locks, CPU etc). "
                         "Wait events explain *why* the database was slow.",
                )

                # ── AWR Report — Text + HTML ──────────────────────────────────
                if run_awr_report:
                    with st.spinner("Generating AWR report in Text and HTML formats (15–60 seconds)…"):
                        _awr_text = generate_awr_report_text(awr_cfg, int(awr_begin), int(awr_end))
                        _awr_html = generate_awr_report_html(awr_cfg, int(awr_begin), int(awr_end))

                    _text_err = _awr_text.startswith("ERROR:")
                    _html_err = _awr_html.startswith("ERROR:")

                    if _text_err and _html_err:
                        st.error(
                            "❌ Both Text and HTML report generation failed. "
                            "Check the EXECUTE privilege below."
                        )
                        st.code(
                            f"GRANT EXECUTE ON DBMS_WORKLOAD_REPOSITORY TO {awr_cfg['user']};",
                            language="sql",
                        )
                    else:
                        if not _text_err:
                            st.session_state["awr_report_text"] = _awr_text
                        if not _html_err:
                            st.session_state["awr_report_html"] = _awr_html

                        if _text_err:
                            st.warning(f"⚠️ Text report failed: {_awr_text}")
                        elif _html_err:
                            st.warning(f"⚠️ HTML report failed: {_awr_html}")
                        else:
                            st.success("✅ AWR report generated in both Text and HTML formats.")

                if "awr_report_text" in st.session_state or "awr_report_html" in st.session_state:
                    st.markdown(
                        "<div style='background:#eef4ff;border-left:4px solid #4f6ef7;"
                        "border-radius:8px;padding:10px 16px;margin:12px 0;"
                        "color:#3a4f6e;font-size:0.88rem;'>"
                        "📥 <b>Download your AWR report:</b> &nbsp;"
                        "Use <b>Text format</b> for reading in a terminal or text editor.&nbsp;"
                        "Use <b>HTML format</b> to open in a browser for the full Oracle-formatted report "
                        "with colour-coded tables and navigation links."
                        "</div>",
                        unsafe_allow_html=True,
                    )

                    _dl1, _dl2 = st.columns(2)

                    if "awr_report_text" in st.session_state:
                        _awr_txt = st.session_state["awr_report_text"]
                        _dl1.download_button(
                            "⬇️ Download as Text (.txt)",
                            data=_awr_txt,
                            file_name=f"awr_{awr_db}_{awr_begin}_{awr_end}.txt",
                            mime="text/plain",
                            key="dl_awr_txt",
                            use_container_width=True,
                        )

                    if "awr_report_html" in st.session_state:
                        _awr_html = st.session_state["awr_report_html"]
                        _dl2.download_button(
                            "⬇️ Download as HTML (.html)",
                            data=_awr_html.encode("utf-8"),
                            file_name=f"awr_{awr_db}_{awr_begin}_{awr_end}.html",
                            mime="text/html",
                            key="dl_awr_html",
                            use_container_width=True,
                        )

                    # ── Preview tabs ──────────────────────────────────────────
                    _prev_text, _prev_html = st.tabs(["📄 Text Preview", "🌐 HTML Preview"])

                    with _prev_text:
                        if "awr_report_text" in st.session_state:
                            st.code(st.session_state["awr_report_text"], language="text")
                        else:
                            st.info("Text report not available.")

                    with _prev_html:
                        if "awr_report_html" in st.session_state:
                            st.info(
                                "💡 The HTML preview is rendered below. For the best experience "
                                "download the .html file and open it in your browser.",
                                icon=None,
                            )
                            st.components.v1.html(
                                st.session_state["awr_report_html"],
                                height=600,
                                scrolling=True,
                            )
                        else:
                            st.info("HTML report not available.")

                # ── Top SQL Analysis ──────────────────────────────────────────
                if run_top_sql:
                    with st.spinner("Fetching top SQL from AWR…"):
                        try:
                            top_sql_df = query_one_db(
                                awr_cfg, AWR_TOP_SQL_QUERY,
                                {"begin_snap_id": int(awr_begin),
                                 "end_snap_id":   int(awr_end)},
                            )
                            top_sql_df.drop(columns=["DATABASE"], errors="ignore", inplace=True)
                            top_sql_df = _numeric_cols_to_float(top_sql_df)
                            st.session_state["awr_top_sql"] = top_sql_df
                        except Exception as exc:
                            handle_ora_error(exc, awr_cfg["user"])

                if "awr_top_sql" in st.session_state:
                    ts_df = st.session_state["awr_top_sql"]
                    if ts_df.empty:
                        st.info("No SQL found in this snapshot range.")
                    else:
                        st.markdown(
                            '<div class="section-header">🔍 Top SQL by Elapsed Time</div>',
                            unsafe_allow_html=True,
                        )

                        ts_kpi1, ts_kpi2, ts_kpi3 = st.columns(3)
                        ts_kpi1.metric("Distinct SQL_IDs", ts_df["SQL_ID"].nunique())
                        ts_kpi2.metric(
                            "Total DB Time (s)",
                            f"{ts_df['TOTAL_ELAPSED_SEC'].sum():,.1f}",
                        )
                        ts_kpi3.metric(
                            "SQLs with Plan Churn",
                            int((ts_df.groupby("SQL_ID")["PLAN_COUNT"].max() > 1).sum()),
                        )

                        # Treemap by elapsed time
                        ts_agg = (
                            ts_df.groupby("SQL_ID", as_index=False)
                            .agg(
                                TOTAL_ELAPSED_SEC=("TOTAL_ELAPSED_SEC", "sum"),
                                TOTAL_EXECUTIONS =("TOTAL_EXECUTIONS",  "sum"),
                                PLAN_COUNT       =("PLAN_COUNT",        "max"),
                                SQL_TEXT_SHORT   =("SQL_TEXT_SHORT",    "first"),
                            )
                            .sort_values("TOTAL_ELAPSED_SEC", ascending=False)
                            .head(30)
                        )
                        ts_agg["LABEL"] = ts_agg["SQL_ID"] + "<br>" + \
                            ts_agg["TOTAL_ELAPSED_SEC"].map(lambda v: f"{v:,.1f}s")

                        fig_tree = px.treemap(
                            ts_agg,
                            path=["SQL_ID"],
                            values="TOTAL_ELAPSED_SEC",
                            color="PLAN_COUNT",
                            color_continuous_scale=["#4ade80", "#facc15", "#f87171"],
                            title="Top 30 SQLs by Total Elapsed Time (colour = plan churn)",
                            custom_data=["SQL_TEXT_SHORT", "TOTAL_EXECUTIONS", "PLAN_COUNT"],
                        )
                        fig_tree.update_traces(
                            hovertemplate=(
                                "<b>%{label}</b><br>"
                                "Elapsed: %{value:,.1f}s<br>"
                                "Executions: %{customdata[1]:,.0f}<br>"
                                "Plans: %{customdata[2]}<br>"
                                "SQL: %{customdata[0]}<extra></extra>"
                            ),
                            textfont_color="#1e2a3a",
                        )
                        fig_tree.update_layout(
                            paper_bgcolor="#ffffff", font_color="#1e2a3a",
                            title_font_color="#2e55c8",
                            margin=dict(t=50, b=10, l=10, r=10),
                            coloraxis_colorbar=dict(
                                title=dict(text="Plans", font=dict(color="#1e2a3a")),
                                tickfont=dict(color="#5a7ab0"),
                            ),
                        )
                        st.plotly_chart(fig_tree, use_container_width=True)

                        # Bar: top 15 by elapsed
                        fig_ts_bar = px.bar(
                            ts_agg.head(15).sort_values("TOTAL_ELAPSED_SEC"),
                            x="TOTAL_ELAPSED_SEC", y="SQL_ID", orientation="h",
                            color="PLAN_COUNT",
                            color_continuous_scale=["#4ade80", "#facc15", "#f87171"],
                            text="TOTAL_ELAPSED_SEC",
                            title="Top 15 SQLs — Total Elapsed Time (seconds)",
                            labels={"TOTAL_ELAPSED_SEC": "Total Elapsed (s)", "SQL_ID": "SQL ID"},
                        )
                        fig_ts_bar.update_traces(
                            texttemplate="%{text:,.1f}s", textposition="outside",
                            textfont_color="#1e2a3a",
                        )
                        fig_ts_bar.update_layout(
                            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                            font_color="#1e2a3a", title_font_color="#2e55c8",
                            xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                            yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                            coloraxis_showscale=False,
                            margin=dict(t=40, b=20, l=10, r=10),
                            height=max(300, len(ts_agg.head(15)) * 38),
                        )
                        st.plotly_chart(fig_ts_bar, use_container_width=True)

                        with st.expander("📋 Full Top SQL Table"):
                            st.dataframe(
                                ts_df[[
                                    "SQL_ID", "PLAN_HASH_VALUE", "SQL_TEXT_SHORT",
                                    "TOTAL_EXECUTIONS", "TOTAL_ELAPSED_SEC", "AVG_ELAPSED_SEC",
                                    "TOTAL_CPU_SEC", "TOTAL_BUFFER_GETS", "TOTAL_DISK_READS",
                                    "PLAN_COUNT",
                                ]].style.background_gradient(
                                    subset=["TOTAL_ELAPSED_SEC"], cmap="RdYlGn_r"
                                ),
                                use_container_width=True, hide_index=True,
                            )
                            st.download_button(
                                "⬇️ Export Top SQL CSV",
                                data=ts_df.to_csv(index=False),
                                file_name=f"awr_top_sql_{awr_db}_{awr_begin}_{awr_end}.csv",
                                mime="text/csv",
                                key="dl_top_sql",
                            )

                # ── Wait Events ───────────────────────────────────────────────
                if run_wait_events:
                    with st.spinner("Fetching wait event data…"):
                        try:
                            wait_df = query_one_db(
                                awr_cfg, AWR_WAIT_EVENTS_QUERY,
                                {"begin_snap_id": int(awr_begin),
                                 "end_snap_id":   int(awr_end)},
                            )
                            wait_df.drop(columns=["DATABASE"], errors="ignore", inplace=True)
                            wait_df = _numeric_cols_to_float(wait_df)
                            st.session_state["awr_wait_df"] = wait_df
                        except Exception as exc:
                            handle_ora_error(exc, awr_cfg["user"])

                if "awr_wait_df" in st.session_state:
                    wait_df = st.session_state["awr_wait_df"]
                    if wait_df.empty:
                        st.info("No wait event data for this range.")
                    else:
                        st.markdown(
                            '<div class="section-header">⏳ Top Wait Events</div>',
                            unsafe_allow_html=True,
                        )
                        fig_wait = px.bar(
                            wait_df.head(15).sort_values("TIME_WAITED_SEC"),
                            x="TIME_WAITED_SEC", y="EVENT_NAME", orientation="h",
                            color="WAIT_CLASS",
                            text="TIME_WAITED_SEC",
                            title="Top 15 Wait Events by Time Waited (seconds, FG sessions)",
                            labels={"TIME_WAITED_SEC": "Time Waited (s)",
                                    "EVENT_NAME": "Wait Event"},
                        )
                        fig_wait.update_traces(
                            texttemplate="%{text:,.2f}s", textposition="outside",
                            textfont_color="#1e2a3a",
                        )
                        fig_wait.update_layout(
                            paper_bgcolor="#ffffff", plot_bgcolor="#f8faff",
                            font_color="#1e2a3a", title_font_color="#2e55c8",
                            xaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                            yaxis=dict(tickfont=dict(color="#5a7ab0"), gridcolor="#dce6fb"),
                            legend=dict(font=dict(color="#1e2a3a"), bgcolor="#ffffff"),
                            margin=dict(t=40, b=20, l=10, r=10),
                            height=max(300, len(wait_df.head(15)) * 38),
                        )
                        st.plotly_chart(fig_wait, use_container_width=True)

                        # Wait class donut
                        wc_agg = (
                            wait_df.groupby("WAIT_CLASS", as_index=False)
                            ["TIME_WAITED_SEC"].sum()
                            .sort_values("TIME_WAITED_SEC", ascending=False)
                        )
                        fig_wc = px.pie(
                            wc_agg, names="WAIT_CLASS", values="TIME_WAITED_SEC",
                            hole=0.5, title="Wait Time by Class",
                        )
                        fig_wc.update_layout(
                            paper_bgcolor="#ffffff", font_color="#1e2a3a",
                            title_font_color="#2e55c8",
                            legend=dict(font=dict(color="#1e2a3a")),
                            margin=dict(t=40, b=10, l=10, r=10),
                        )
                        fig_wc.update_traces(textfont_color="#1e2a3a")
                        st.plotly_chart(fig_wc, use_container_width=True)

                        with st.expander("📋 Full Wait Events Table"):
                            st.dataframe(
                                wait_df.style.background_gradient(
                                    subset=["TIME_WAITED_SEC"], cmap="RdYlGn_r"
                                ),
                                use_container_width=True, hide_index=True,
                            )

                # ── Performance Analysis ──────────────────────────────────────
                st.markdown("---")
                st.markdown(
                    '<div class="section-header">🧠 DB Performance Analysis & Recommendations</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    "<p style='color:#5a7ab0;'>Interprets your AWR data and explains "
                    "what is happening in the database in plain language, "
                    "with specific recommendations to fix each issue.</p>",
                    unsafe_allow_html=True,
                )

                run_analysis = st.button(
                    "🧠 Analyze DB Performance", type="primary", key="run_analysis"
                )

                if run_analysis:
                    _wdf = st.session_state.get("awr_wait_df")
                    _tdf = st.session_state.get("awr_top_sql")
                    st.session_state["awr_analysis"] = analyze_awr_performance(_wdf, _tdf)

                if "awr_analysis" in st.session_state:
                    ana = st.session_state["awr_analysis"]
                    score  = ana["health_score"]
                    status = ana["status"]
                    sc     = ana["status_color"]
                    sb     = ana["status_bg"]

                    # Health score banner
                    st.markdown(
                        f"<div style='background:{sb};border:2px solid {sc};"
                        f"border-radius:12px;padding:18px 24px;margin:16px 0;'>"
                        f"<div style='display:flex;align-items:center;gap:20px;'>"
                        f"<div style='font-size:3rem;font-weight:800;color:{sc};'>{score}</div>"
                        f"<div>"
                        f"<div style='font-size:1.3rem;font-weight:700;color:{sc};'>"
                        f"Health Score — {status}</div>"
                        f"<div style='color:#3a4f6e;margin-top:4px;font-size:0.95rem;'>"
                        f"{ana['summary']}</div>"
                        f"</div></div></div>",
                        unsafe_allow_html=True,
                    )

                    # Score gauge bar
                    fig_gauge = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=score,
                        title={"text": "DB Health Score", "font": {"color": "#1e2a3a"}},
                        gauge={
                            "axis": {"range": [0, 100],
                                     "tickcolor": "#5a7ab0",
                                     "tickfont": {"color": "#5a7ab0"}},
                            "bar": {"color": sc},
                            "bgcolor": "#ffffff",
                            "bordercolor": "#dce6fb",
                            "steps": [
                                {"range": [0,  60], "color": "#fde8e8"},
                                {"range": [60, 85], "color": "#fefce8"},
                                {"range": [85,100], "color": "#dcfce7"},
                            ],
                            "threshold": {
                                "line": {"color": "#ffffff", "width": 2},
                                "thickness": 0.8, "value": score,
                            },
                        },
                        number={"font": {"color": sc}},
                    ))
                    fig_gauge.update_layout(
                        paper_bgcolor="#ffffff", font_color="#1e2a3a",
                        height=260, margin=dict(t=40, b=10, l=30, r=30),
                    )
                    st.plotly_chart(fig_gauge, use_container_width=True)

                    # Findings
                    findings = ana["findings"]
                    criticals = [f for f in findings if f["severity"] == "critical"]
                    warnings  = [f for f in findings if f["severity"] == "warning"]
                    infos     = [f for f in findings if f["severity"] == "info"]

                    sev_meta = {
                        "critical": ("🔴 Critical Issues",   "#dc2626", "#fff1f0", "#ef4444"),
                        "warning":  ("⚠️ Warnings",          "#d97706", "#fffbeb", "#f59e0b"),
                        "info":     ("ℹ️ Informational",     "#2563eb", "#eff6ff", "#60a5fa"),
                    }

                    for sev_key, items in [("critical", criticals),
                                           ("warning",  warnings),
                                           ("info",     infos)]:
                        if not items:
                            continue
                        label, fc, bg, bc = sev_meta[sev_key]
                        st.markdown(
                            f"<h4 style='color:{fc};margin-top:24px;'>{label} ({len(items)})</h4>",
                            unsafe_allow_html=True,
                        )
                        for finding in items:
                            st.markdown(
                                f"<div style='background:{bg};border-left:4px solid {bc};"
                                f"border-radius:8px;padding:14px 18px;margin-bottom:12px;'>"
                                f"<div style='font-size:1rem;font-weight:700;color:{fc};"
                                f"margin-bottom:6px;'>{finding['title']}</div>"
                                f"<div style='color:#3a4f6e;font-size:0.9rem;"
                                f"margin-bottom:8px;'>"
                                f"<b style='color:#5a7ab0;'>What is happening:</b> "
                                f"{finding['detail']}</div>"
                                f"<div style='color:#1e5c3a;font-size:0.88rem;'>"
                                f"<b style='color:#15803d;'>💡 Recommendation:</b> "
                                f"{finding['recommendation']}</div>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                    # Export analysis as text report
                    report_lines = [
                        f"Oracle DB Performance Analysis Report",
                        f"Database : {awr_db}",
                        f"Snapshot : {awr_begin} → {awr_end}",
                        f"Health Score: {score}/100  ({status})",
                        f"Summary : {ana['summary']}",
                        "",
                        "=" * 70,
                    ]
                    for f in findings:
                        report_lines += [
                            f"\n[{f['severity'].upper()}] {f['title']}",
                            f"  What is happening : {f['detail']}",
                            f"  Recommendation    : {f['recommendation']}",
                        ]
                    st.download_button(
                        "⬇️ Export Analysis Report (.txt)",
                        data="\n".join(report_lines),
                        file_name=f"awr_analysis_{awr_db}_{awr_begin}_{awr_end}.txt",
                        mime="text/plain",
                        key="dl_analysis",
                    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — Ask the Dashboard (Q&A on loaded data)
# ══════════════════════════════════════════════════════════════════════════════
with tab_ask:
    st.markdown('<div class="section-header">💬 Ask about this dashboard</div>',
                unsafe_allow_html=True)

    # ── LLM status banner (Ollama only) ──────────────────────────────────────
    _ollama_up    = ollama_is_running()
    _ollama_ready = _ollama_up and ollama_model_available()

    if _ollama_ready:
        st.markdown(
            f"<div style='background:#f0fdf4;border:1.5px solid #86efac;border-radius:8px;"
            f"padding:10px 16px;margin-bottom:12px;display:flex;align-items:center;gap:10px;'>"
            f"<span style='font-size:1.2rem;'>✅</span>"
            f"<span style='color:#16a34a;font-weight:700;'>LLM active — Ollama ({OLLAMA_MODEL}) running locally</span>"
            f"<span style='color:#15803d;font-size:0.85rem;margin-left:6px;'>"
            f"Free local AI — no internet required.</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    elif _ollama_up:
        st.markdown(
            f"<div style='background:#eff6ff;border:1.5px solid #93c5fd;border-radius:8px;"
            f"padding:10px 16px;margin-bottom:12px;display:flex;align-items:center;gap:10px;'>"
            f"<span style='font-size:1.2rem;'>🔄</span>"
            f"<span style='color:#2563eb;font-weight:700;'>Ollama running — model not pulled yet</span>"
            f"<span style='color:#1d4ed8;font-size:0.85rem;margin-left:6px;'>"
            f"Run: <code>ollama pull {OLLAMA_MODEL}</code> in a terminal, then refresh.</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='background:#fffbeb;border:1.5px solid #fcd34d;border-radius:8px;"
            "padding:10px 16px;margin-bottom:12px;display:flex;align-items:center;gap:10px;'>"
            "<span style='font-size:1.2rem;'>⚠️</span>"
            "<span style='color:#d97706;font-weight:700;'>Ollama not running — using built-in summaries</span>"
            "<span style='color:#fcd34d;font-size:0.85rem;margin-left:6px;'>"
            "Open a terminal and run: <code>ollama serve</code></span>"
            "</div>",
            unsafe_allow_html=True,
        )

    dash_ctx = build_dashboard_context(
        summary_df,
        module_df,
        active_cfgs,
        sql_id_filter,
        schema_filter,
        module_filter,
    )

    if "dash_chat" not in st.session_state:
        st.session_state["dash_chat"] = []

    # ── Example question chips (shown only when chat is empty) ────────────────
    if not st.session_state["dash_chat"]:
        st.markdown(
            "<div style='color:#5a7ab0;font-size:0.9rem;font-weight:600;"
            "margin-bottom:8px;'>💡 Not sure what to ask? Try one of these:</div>",
            unsafe_allow_html=True,
        )
        _example_qs = [
            "Which database has the most plan fluctuations?",
            "List the top 5 unstable SQL IDs",
            "Is my database healthy?",
            "Which module is causing the most issues?",
            "How many SQLs are stable vs fluctuating?",
            "What is the worst performing SQL?",
            "List all databases and their health status",
        ]
        _eq_cols = st.columns(3)
        for _i, _q in enumerate(_example_qs):
            if _eq_cols[_i % 3].button(f"❓ {_q}", key=f"eq_{_i}", use_container_width=True):
                st.session_state["dash_chat"].append({"role": "user", "content": _q})
                st.session_state["_pending_ask"] = _q
                st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)

    for msg in st.session_state["dash_chat"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Process a question queued by an example chip
    if "_pending_ask" in st.session_state:
        user_q = st.session_state.pop("_pending_ask")
    else:
        user_q = st.chat_input("Ask a question about the numbers and tables loaded above…")
    if user_q:
        st.session_state["dash_chat"].append({"role": "user", "content": user_q})

        answer_text = ""
        err_note    = None

        if _ollama_ready:
            with st.spinner(f"Asking Ollama ({OLLAMA_MODEL}) locally…"):
                answer_text, err_note = ask_ollama(user_q, dash_ctx)
            if err_note or not answer_text.strip():
                if err_note:
                    st.warning(f"Ollama error: {err_note}  Using built-in summary.")
                answer_text = _answer_dashboard_heuristic(
                    user_q, summary_df, module_df, active_cfgs
                )
        else:
            answer_text = _answer_dashboard_heuristic(
                user_q, summary_df, module_df, active_cfgs
            )

        st.session_state["dash_chat"].append({"role": "assistant", "content": answer_text})
        st.rerun()

    with st.expander("Preview context sent to the assistant (first 8000 chars)", expanded=False):
        preview = dash_ctx if len(dash_ctx) <= 8000 else dash_ctx[:8000] + "\n… [truncated for preview]"
        st.code(preview, language="text")

    c_clear, c_hint = st.columns(2)
    with c_clear:
        if st.button("Clear chat history"):
            st.session_state["dash_chat"] = []
            st.rerun()
    with c_hint:
        st.caption(
            "Tip: reload data from the sidebar to refresh answers; chat clears on **Load Dashboard**."
        )
