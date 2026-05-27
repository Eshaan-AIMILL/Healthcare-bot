"""
Text2SQL Tool
Converts a natural-language question into a safe, parameterised SQLite SELECT
query using the LLM, then executes it against the local SQLite database.

Schema context is loaded from app/schemas/schema.json and
app/schemas/prompts.json — never hardcoded here.
"""
import json
import os
from functools import lru_cache
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.utils.logger import logger

_SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemas")


@lru_cache(maxsize=1)
def _load_schema_context() -> str:
    """
    Build a concise, LLM-friendly schema description from schema.json.
    Cached after first load — file is read once per process.
    Includes table descriptions, column names + types + descriptions,
    join patterns, and query rules.  No raw SQL examples are hardcoded.
    """
    schema_path  = os.path.join(_SCHEMAS_DIR, "schema.json")
    prompts_path = os.path.join(_SCHEMAS_DIR, "prompts.json")

    with open(schema_path,  encoding="utf-8") as f:
        schema = json.load(f)
    with open(prompts_path, encoding="utf-8") as f:
        prompts = json.load(f)

    lines: list[str] = []
    lines.append(f"DATABASE: {schema['database']} ({schema['engine']})")
    lines.append(f"PURPOSE : {schema['description']}\n")

    # ── Tables ────────────────────────────────────────────────────────────────
    lines.append("TABLES:")
    for table_name, table in schema["tables"].items():
        lines.append(f"\n  {table_name}")
        lines.append(f"    Description : {table['description']}")
        if "primary_key" in table:
            lines.append(f"    Primary key : {table['primary_key']}")
        if "foreign_keys" in table:
            fks = ", ".join(f"{k} → {v}" for k, v in table["foreign_keys"].items())
            lines.append(f"    Foreign keys: {fks}")

        lines.append("    Columns:")
        for col_name, col in table["columns"].items():
            lines.append(
                f"      {col_name} ({col['type']}): {col['description']}"
            )

        if "useful_aggregations" in table:
            lines.append("    Useful aggregations (examples only — do NOT copy verbatim):")
            for agg in table["useful_aggregations"]:
                lines.append(f"      • {agg}")

        if "sla_thresholds" in table:
            lines.append(f"    SLA thresholds: {table['sla_thresholds']}")
        if "sla_windows" in table:
            lines.append(f"    SLA windows: {table['sla_windows']}")

    # ── Join patterns ─────────────────────────────────────────────────────────
    lines.append("\nCOMMON JOIN PATTERNS (use as reference — adapt to the question):")
    for pattern_name, pattern_sql in schema["join_patterns"].items():
        lines.append(f"  {pattern_name}:")
        lines.append(f"    {pattern_sql}")

    # ── Query rules ───────────────────────────────────────────────────────────
    lines.append("\nQUERY RULES (must follow every rule):")
    for rule in schema["query_rules"]:
        lines.append(f"  • {rule}")

    # ── Text2SQL rules from prompts.json ──────────────────────────────────────
    lines.append("\nADDITIONAL TEXT2SQL RULES:")
    for rule in prompts["text2sql_rules"]:
        lines.append(f"  • {rule}")

    return "\n".join(lines)


_SYSTEM_PROMPT_TEMPLATE = """\
You are a SQL query generator for a healthcare SQLite database.
Your job is to convert the user's natural-language question into one safe,
parameterised SQLite SELECT query based on the schema below.

{schema}

Respond with valid JSON only. No markdown fences. No extra keys.
Schema: {{"sql": "<SELECT query with ? placeholders>", "params": [<values>], "explanation": "<one sentence>"}}
"""

_USER_PROMPT_TEMPLATE = "Question: {question}"


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )


async def run_text2sql(question: str, db: AsyncSession) -> list[dict[str, Any]]:
    """
    Convert *question* to a parameterised SQL query using the LLM,
    execute it against SQLite, and return results as a list of dicts.
    Returns an empty list on any error — callers must handle gracefully.
    """
    schema_context = _load_schema_context()
    system_prompt  = _SYSTEM_PROMPT_TEMPLATE.format(schema=schema_context)

    llm = _build_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=_USER_PROMPT_TEMPLATE.format(question=question)),
    ]

    try:
        response = await llm.ainvoke(messages)
        payload     = json.loads(response.content)
        sql_query   = payload.get("sql", "").strip()
        params      = payload.get("params", [])
        explanation = payload.get("explanation", "")

        logger.debug(
            f"Text2SQL | sql={sql_query!r} | params={params} | {explanation}"
        )

        # ── Safety gate: only SELECT allowed ─────────────────────────────────
        if not sql_query.upper().startswith("SELECT"):
            logger.warning(
                f"Text2SQL blocked non-SELECT query: {sql_query!r}"
            )
            return []

        # ── Block dangerous keywords even inside SELECT ───────────────────────
        upper = sql_query.upper()
        for forbidden in ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER",
                          "CREATE", "ATTACH", "DETACH", "PRAGMA"):
            if forbidden in upper:
                logger.warning(
                    f"Text2SQL blocked query containing '{forbidden}': {sql_query!r}"
                )
                return []

        result  = await db.execute(text(sql_query), params)
        columns = list(result.keys())
        rows    = [dict(zip(columns, row)) for row in result.fetchall()]

        logger.info(f"Text2SQL returned {len(rows)} rows for: {question!r}")
        return rows

    except json.JSONDecodeError as exc:
        logger.error(f"Text2SQL: LLM returned non-JSON — {exc}")
        return []
    except Exception as exc:
        logger.error(f"Text2SQL execution failed: {exc}")
        return []
