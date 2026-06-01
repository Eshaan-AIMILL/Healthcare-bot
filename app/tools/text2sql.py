# """
# Text2SQL Tool
# Converts a natural-language question into a safe, parameterised SQLite SELECT
# query using the LLM, then executes it against the local SQLite database.

# Schema context is now dynamically introspected from the SQLAlchemy models.
# Implements Multi-Layer SQL Safety (Introspection, Validation, Auto-Repair, Safe Execution).
# """
# import json
# import os
# from functools import lru_cache
# from typing import Any

# from langchain_ollama import ChatOllama
# from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
# from sqlalchemy import text
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.config import settings
# from app.utils.logger import logger
# from app.db.introspection import build_schema_metadata
# from app.tools.sql_validator import validate_sql_against_schema
# from app.core.security import SecurityContext
# from app.core.rbac import RBACManager, Role
# from app.utils.prompts import TEXT2SQL_SYSTEM, TEXT2SQL_USER



# def _build_llm() -> ChatOllama:
#     return ChatOllama(
#         model=settings.ollama_text2sql_model,
#         base_url=settings.ollama_base_url,
#         temperature=0.0,
#     )

# async def safe_execute_query(db: AsyncSession, sql_query: str, params: list | dict) -> list[dict[str, Any]]:
#     # Layer 4: Safe Execution Wrapper.
#     # Normalizes parameters and prevents malformed execute calls.
#     import re
    
#     if "?" in sql_query:
#         parts = sql_query.split("?")
#         new_query_parts = []
#         new_params = {}
#         param_values = []
        
#         if isinstance(params, dict):
#             # Extract numeric parts of keys if they exist to sort them
#             def get_num(key):
#                 nums = [int(s) for s in re.findall(r"\d+", key)]
#                 return nums[0] if nums else 999
            
#             # Sort keys based on their numeric value, fallback to alphabetical
#             sorted_keys = sorted(params.keys(), key=lambda x: (get_num(x), x))
#             param_values = [params[k] for k in sorted_keys]
#         elif isinstance(params, (list, tuple)):
#             param_values = list(params)
            
#         for i in range(len(parts) - 1):
#             placeholder = f"p{i}"
#             new_query_parts.append(parts[i])
#             new_query_parts.append(f":{placeholder}")
#             val = param_values[i] if i < len(param_values) else None
#             new_params[placeholder] = val
            
#         new_query_parts.append(parts[-1])
#         sql_query = "".join(new_query_parts)
#         params = new_params
#     elif isinstance(params, list):
#         params = tuple(params)
        
#     try:
#         result = await db.execute(text(sql_query), params)
#         if result.returns_rows:
#             columns = list(result.keys())
#             rows = [dict(zip(columns, row)) for row in result.fetchall()]
#             return rows
#         else:
#             await db.commit()
#             return [{"status": "success", "rows_affected": result.rowcount}]
#     except Exception as e:
#         logger.error(f"Execution Error: {e}")
#         raise e

# async def run_text2sql(question: str, db: AsyncSession, security_context: SecurityContext = None, domain: str = "general") -> list[dict[str, Any]]:
#     """
#     Convert *question* to a parameterised SQL query using the LLM,
#     validate it, auto-repair if needed, execute it, and return results.
#     Includes Independent Tool-Level Authorization.
#     """
#     if not security_context:
#         logger.error("Text2SQL Tool blocked: Missing SecurityContext.")
#         raise PermissionError("Independent Tool Authorization Failed: Missing SecurityContext.")
        
#     try:
#         user_role = Role(security_context.enterprise_role)
#     except ValueError:
#         user_role = Role.GUEST
        
#     if domain != "general" and not RBACManager.can_access_domain(user_role, domain):
#         logger.critical(f"Text2SQL Tool Authorization Violation! Role {user_role.value} attempted to query {domain}.")
#         raise PermissionError(f"Independent Tool Authorization Failed: Role {user_role.value} cannot query {domain}.")

#     from datetime import datetime
#     current_date = datetime.now().strftime("%Y-%m-%d")
#     schema_context = build_schema_metadata()
#     system_prompt = TEXT2SQL_SYSTEM.format(
#         schema=schema_context,
#         domain=domain,
#         current_date=current_date
#     )

#     llm = _build_llm()
#     messages = [
#         SystemMessage(content=system_prompt),
#         HumanMessage(content=TEXT2SQL_USER.format(question=question)),
#     ]

#     max_retries = 3
    
#     for attempt in range(max_retries):
#         try:
#             response = await llm.ainvoke(messages)
#             content = response.content.strip()
            
#             if content.startswith("ERROR:"):
#                 logger.warning(f"Text2SQL Graceful Rejection: {content}")
#                 return []
                
#             import re
            
#             sql_match = re.search(r"<sql>(.*?)</sql>", content, re.DOTALL)
#             if not sql_match:
#                 logger.error("Text2SQL: Could not find <sql> tags in response.")
#                 messages.append(AIMessage(content=content))
#                 messages.append(HumanMessage(content="You must enclose your query in <sql>...</sql> tags. Try again."))
#                 continue
                
#             sql_query = sql_match.group(1).strip()
            
#             params_match = re.search(r"<params>(.*?)</params>", content, re.DOTALL)
#             params = {}
#             if params_match:
#                 param_str = params_match.group(1).strip()
#                 try:
#                     params = json.loads(param_str)
#                 except json.JSONDecodeError:
#                     try:
#                         import ast
#                         params = ast.literal_eval(param_str)
#                         if not isinstance(params, dict):
#                             raise ValueError("Params must be a dict")
#                     except Exception as e:
#                         logger.error(f"Text2SQL: <params> parsing failed — {e}")
#                         messages.append(AIMessage(content=content))
#                         messages.append(HumanMessage(content="Your <params> block is not valid JSON. You must use double quotes for keys/values. Try again."))
#                         continue
                    
#             thinking_match = re.search(r"<thinking>(.*?)</thinking>", content, re.DOTALL)
#             explanation = thinking_match.group(1).strip() if thinking_match else ""

#             logger.debug(
#                 f"Text2SQL [Attempt {attempt+1}] | sql={sql_query!r} | params={params} | {explanation}"
#             )

#             # Layer 2: AST-based SQL Validation
#             validation_error = validate_sql_against_schema(sql_query)
            
#             if validation_error:
#                 logger.warning(f"Text2SQL validation failed: {validation_error}")
#                 # Layer 3: Auto-Repair via LLM Feedback
#                 messages.append(AIMessage(content=response.content))
#                 messages.append(HumanMessage(content=f"Your previous query was invalid. Fix this error and return the corrected query inside <sql>...</sql> tags: {validation_error}"))
#                 continue # Retry

#             # Execution (Layer 4)
#             rows = await safe_execute_query(db, sql_query, params)
#             logger.info(f"Text2SQL returned {len(rows)} rows for: {question!r}")
#             return rows

#         except Exception as exc:
#             logger.error(f"Text2SQL execution failed: {exc}")
#             messages.append(AIMessage(content=response.content))
#             messages.append(HumanMessage(content=f"Your previous query was invalid. Fix this execution error and return the corrected query inside <sql>...</sql> tags: {exc}"))
#             continue
            
#     logger.error("Text2SQL: Max retries reached. Query generation failed.")
#     return []

"""
Text2SQL Tool — SLM-Optimized Edition
======================================
Converts a natural-language question into a safe, parameterised SQLite SELECT
query using a local 7B model (qwen2.5:7b via Ollama), then executes it against
the application SQLite database.

Key changes vs. the original:
1. Domain-scoped schema injection — only tables relevant to the domain are
   passed to the model, halving prompt token count for single-domain queries.
2. Precision error recovery — AST validation errors and SQL execution errors
   are parsed and reformulated into tight, directive correction prompts.  The
   raw exception stack is never forwarded to the model.
3. Structured correction prompt factory — every retry message is generated by
   _build_correction_prompt(), which is the single place that translates a
   machine error into a minimal, SLM-safe instruction.
4. Response-parse hardening — param-block parsing is isolated so a bad JSON
   block triggers an immediate targeted correction, not a generic retry.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rbac import RBACManager, Role
from app.core.security import SecurityContext
from app.db.introspection import (
    build_schema_metadata,
    get_all_table_names,
    get_valid_columns_for_table,
)
from app.tools.sql_validator import validate_sql_against_schema
from app.utils.logger import logger
from app.utils.prompts import TEXT2SQL_SYSTEM, TEXT2SQL_USER, select_few_shot_examples


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_text2sql_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
    )


# ---------------------------------------------------------------------------
# Precision error formatter  (the core SLM optimization)
# ---------------------------------------------------------------------------

# Regex patterns used to extract entity names from common SQLite error strings.
_RE_NO_SUCH_COLUMN = re.compile(
    r"no such column[:\s]+['\"]?([a-zA-Z0-9_.]+)['\"]?", re.IGNORECASE
)
_RE_NO_SUCH_TABLE = re.compile(
    r"no such table[:\s]+['\"]?([a-zA-Z0-9_]+)['\"]?", re.IGNORECASE
)
_RE_SYNTAX_NEAR = re.compile(
    r'syntax error.*near[:\s]+"([^"]+)"', re.IGNORECASE
)
_RE_AMBIGUOUS_COL = re.compile(
    r"ambiguous column name[:\s]+['\"]?([a-zA-Z0-9_.]+)['\"]?", re.IGNORECASE
)


def _build_correction_prompt(error_source: str, raw_error: str, sql_query: str) -> str:
    """
    Translate a machine error into a minimal, directive correction message
    that a 7B model can act on without spiralling into hallucinations.

    Parameters
    ----------
    error_source:
        ``"validation"`` (AST pre-execution check) or ``"execution"``
        (SQLite runtime error).
    raw_error:
        The raw error string from the validator or the DB driver.
    sql_query:
        The SQL query that triggered the error (used for context extraction).

    Returns
    -------
    str
        A concise, directive correction prompt safe for a 7B model.
    """
    known_tables = get_all_table_names()

    # ── No such column ──────────────────────────────────────────────────────
    col_match = _RE_NO_SUCH_COLUMN.search(raw_error)
    if col_match:
        bad_col = col_match.group(1)
        # Try to infer the table the model was targeting from the bad col ref.
        table_hint = ""
        if "." in bad_col:
            tbl_name, col_name = bad_col.split(".", 1)
            valid = get_valid_columns_for_table(tbl_name)
            if valid:
                table_hint = (
                    f"Table `{tbl_name}` exists. "
                    f"Its valid columns are: {', '.join(valid)}."
                )
            bad_col = col_name
        else:
            # Search all tables in the SQL for a match hint
            for tbl in known_tables:
                if tbl.lower() in sql_query.lower():
                    valid = get_valid_columns_for_table(tbl)
                    if bad_col in [c.split(".")[-1] for c in valid]:
                        table_hint = (
                            f"Did you mean a column from `{tbl}`? "
                            f"Valid columns: {', '.join(valid)}."
                        )
                        break

        return (
            f"CORRECTION NEEDED — Column Error.\n"
            f"Column `{bad_col}` does not exist in the schema.\n"
            f"{table_hint}\n"
            f"Fix the column name and return the corrected query in <sql>...</sql> tags."
        )

    # ── No such table ───────────────────────────────────────────────────────
    tbl_match = _RE_NO_SUCH_TABLE.search(raw_error)
    if tbl_match:
        bad_tbl = tbl_match.group(1)
        return (
            f"CORRECTION NEEDED — Table Error.\n"
            f"Table `{bad_tbl}` does not exist.\n"
            f"The ONLY valid tables are: {', '.join(known_tables)}.\n"
            f"Fix the table name and return the corrected query in <sql>...</sql> tags."
        )

    # ── Ambiguous column ────────────────────────────────────────────────────
    amb_match = _RE_AMBIGUOUS_COL.search(raw_error)
    if amb_match:
        bad_col = amb_match.group(1)
        return (
            f"CORRECTION NEEDED — Ambiguous Column.\n"
            f"Column `{bad_col}` appears in more than one table in your JOIN.\n"
            f"Prefix it with the table alias, e.g. `t.{bad_col}`.\n"
            f"Return the corrected query in <sql>...</sql> tags."
        )

    # ── Syntax error ────────────────────────────────────────────────────────
    syn_match = _RE_SYNTAX_NEAR.search(raw_error)
    if syn_match:
        near_token = syn_match.group(1)
        return (
            f"CORRECTION NEEDED — SQL Syntax Error.\n"
            f'SQLite reported a syntax error near the token: "{near_token}".\n'
            f"Review that clause, ensure keywords are correct SQLite syntax, "
            f"and return the corrected query in <sql>...</sql> tags."
        )

    # ── AST validation error (pre-execution) ────────────────────────────────
    if error_source == "validation":
        # The validator returns structured messages like:
        # "INSERT statements are not allowed" or "Column X not in schema"
        return (
            f"CORRECTION NEEDED — Query Validation Failed.\n"
            f"Reason: {raw_error}\n"
            f"Fix only this specific issue and return the corrected query "
            f"in <sql>...</sql> tags. Do not change other parts of the query."
        )

    # ── Fallback — keep it brief ─────────────────────────────────────────
    # Never dump a raw traceback into the model prompt.
    brief = raw_error[:200].replace("\n", " ")
    return (
        f"CORRECTION NEEDED.\n"
        f"The previous query failed: {brief}\n"
        f"Fix the issue and return the corrected query in <sql>...</sql> tags."
    )


def _build_params_correction_prompt(bad_param_str: str) -> str:
    return (
        "CORRECTION NEEDED — Invalid <params> block.\n"
        f"Your <params> block contained invalid JSON: {bad_param_str[:120]}\n"
        "Rules: use double quotes for all keys and string values, "
        "e.g. <params>{\"status\": \"Rejected\"}</params>.\n"
        "Return the corrected <params> block together with your unchanged "
        "<sql>...</sql> block."
    )


# ---------------------------------------------------------------------------
# Safe execution wrapper (Layer 4)
# ---------------------------------------------------------------------------

async def safe_execute_query(
    db: AsyncSession,
    sql_query: str,
    params: list | dict,
) -> list[dict[str, Any]]:
    """
    Normalize parameters and execute *sql_query* against *db*.

    Handles both ``?``-style positional placeholders (legacy) and
    ``:name``-style named placeholders (preferred output from the LLM).
    """
    # Normalize positional ? → :p0, :p1, … named params
    if "?" in sql_query:
        parts = sql_query.split("?")
        new_parts: list[str] = []
        new_named: dict[str, Any] = {}

        param_values: list[Any] = []
        if isinstance(params, dict):
            # Sort by numeric suffix if present; keeps deterministic order.
            sorted_keys = sorted(
                params.keys(),
                key=lambda k: (int(re.search(r"\d+", k).group()) if re.search(r"\d+", k) else 999, k),
            )
            param_values = [params[k] for k in sorted_keys]
        elif isinstance(params, (list, tuple)):
            param_values = list(params)

        for i, part in enumerate(parts[:-1]):
            placeholder = f"p{i}"
            new_parts.append(part)
            new_parts.append(f":{placeholder}")
            new_named[placeholder] = param_values[i] if i < len(param_values) else None

        new_parts.append(parts[-1])
        sql_query = "".join(new_parts)
        params = new_named

    elif isinstance(params, list):
        params = tuple(params)

    result = await db.execute(text(sql_query), params)
    if result.returns_rows:
        columns = list(result.keys())
        return [dict(zip(columns, row)) for row in result.fetchall()]
    else:
        await db.commit()
        return [{"status": "success", "rows_affected": result.rowcount}]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_text2sql(
    question: str,
    db: AsyncSession,
    security_context: SecurityContext | None = None,
    domain: str = "general",
    domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Convert *question* to a parameterised SQL query, validate, execute,
    and return results.

    Parameters
    ----------
    question:
        The natural-language question from the user.
    db:
        Active async SQLAlchemy session.
    security_context:
        RBAC context for independent tool-level authorization.
    domain:
        Primary domain string — kept for backward compatibility and single-
        domain RBAC checks.
    domains:
        Optional list of domain strings for cross-domain queries.  When
        provided, schema pruning uses the full list.  Falls back to
        ``[domain]`` when omitted.
    """
    # ── Authorization (Layer 1) ──────────────────────────────────────────────
    if not security_context:
        logger.error("Text2SQL Tool blocked: Missing SecurityContext.")
        raise PermissionError(
            "Independent Tool Authorization Failed: Missing SecurityContext."
        )

    try:
        user_role = Role(security_context.enterprise_role)
    except ValueError:
        user_role = Role.GUEST

    if domain != "general" and not RBACManager.can_access_domain(user_role, domain):
        logger.critical(
            "Text2SQL Authorization Violation! "
            f"Role {user_role.value} attempted to query {domain}."
        )
        raise PermissionError(
            f"Independent Tool Authorization Failed: "
            f"Role {user_role.value} cannot query {domain}."
        )

    # ── Schema construction (domain-pruned) ──────────────────────────────────
    active_domains = domains if domains else [domain]
    current_date = datetime.now().strftime("%Y-%m-%d")
    schema_context = build_schema_metadata(domains=active_domains)

    few_shot_block = select_few_shot_examples(
        domain=domain,
        question=question,
        max_examples=2,
    )
    system_prompt = TEXT2SQL_SYSTEM.format(
        schema=schema_context,
        domain=domain,
        current_date=current_date,
        few_shot_examples=few_shot_block,
    )

    llm = _build_llm()
    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=TEXT2SQL_USER.format(question=question)),
    ]

    MAX_RETRIES = 3
    last_ai_content: str = ""

    for attempt in range(MAX_RETRIES):
        try:
            response = await llm.ainvoke(messages)
            content: str = response.content.strip()
            last_ai_content = content

            # ── Graceful rejection ───────────────────────────────────────────
            if content.startswith("ERROR:"):
                logger.warning(f"Text2SQL graceful rejection: {content}")
                return []

            # ── Extract <sql> block ──────────────────────────────────────────
            sql_match = re.search(r"<sql>(.*?)</sql>", content, re.DOTALL)
            if not sql_match:
                logger.warning(f"[Attempt {attempt+1}] No <sql> tags found.")
                messages.append(AIMessage(content=content))
                messages.append(HumanMessage(
                    content=(
                        "CORRECTION NEEDED — Output Format.\n"
                        "You did not wrap your query in <sql>...</sql> tags.\n"
                        "Return your query inside <sql>YOUR QUERY HERE</sql>."
                    )
                ))
                continue

            sql_query = sql_match.group(1).strip()

            # ── Extract <params> block ───────────────────────────────────────
            params: dict = {}
            params_match = re.search(r"<params>(.*?)</params>", content, re.DOTALL)
            if params_match:
                raw_params = params_match.group(1).strip()
                if raw_params and raw_params not in ("{}", "{}"):
                    try:
                        parsed = json.loads(raw_params)
                        if not isinstance(parsed, dict):
                            raise ValueError("Params must be a JSON object.")
                        params = parsed
                    except (json.JSONDecodeError, ValueError):
                        try:
                            parsed = ast.literal_eval(raw_params)
                            if not isinstance(parsed, dict):
                                raise ValueError("Params must be a dict.")
                            params = parsed
                        except Exception:
                            logger.warning(
                                f"[Attempt {attempt+1}] Bad <params> block: {raw_params!r}"
                            )
                            messages.append(AIMessage(content=content))
                            messages.append(HumanMessage(
                                content=_build_params_correction_prompt(raw_params)
                            ))
                            continue

            # ── Debug logging ────────────────────────────────────────────────
            thinking_match = re.search(r"<thinking>(.*?)</thinking>", content, re.DOTALL)
            thinking = thinking_match.group(1).strip() if thinking_match else ""
            logger.debug(
                f"Text2SQL [Attempt {attempt+1}] | "
                f"sql={sql_query!r} | params={params} | thinking_present={bool(thinking)}"
            )

            # ── Layer 2: AST / static SQL validation ────────────────────────
            validation_error: str | None = validate_sql_against_schema(sql_query)
            if validation_error:
                logger.warning(f"[Attempt {attempt+1}] Validation failed: {validation_error}")
                correction = _build_correction_prompt(
                    error_source="validation",
                    raw_error=validation_error,
                    sql_query=sql_query,
                )
                messages.append(AIMessage(content=content))
                messages.append(HumanMessage(content=correction))
                continue  # → Layer 3: auto-repair via focused feedback

            # ── Layer 4: Safe execution ──────────────────────────────────────
            rows = await safe_execute_query(db, sql_query, params)
            logger.info(
                f"Text2SQL success after {attempt+1} attempt(s) | "
                f"{len(rows)} rows | question={question!r}"
            )
            return rows

        except Exception as exc:
            raw_exc = str(exc)
            logger.error(f"[Attempt {attempt+1}] Execution error: {raw_exc}")
            correction = _build_correction_prompt(
                error_source="execution",
                raw_error=raw_exc,
                sql_query=sql_query if "sql_query" in dir() else "",
            )
            messages.append(AIMessage(content=last_ai_content))
            messages.append(HumanMessage(content=correction))
            continue

    logger.error(
        f"Text2SQL: Exhausted {MAX_RETRIES} retries for question={question!r}"
    )
    return []