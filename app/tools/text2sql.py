"""
Text2SQL Tool
Converts a natural-language question into a safe, parameterised SQLite SELECT
query using the LLM, then executes it against the local SQLite database.

Schema context is now dynamically introspected from the SQLAlchemy models.
Implements Multi-Layer SQL Safety (Introspection, Validation, Auto-Repair, Safe Execution).
"""
import json
import os
from functools import lru_cache
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.utils.logger import logger
from app.db.introspection import build_schema_metadata
from app.tools.sql_validator import validate_sql_against_schema
from app.core.security import SecurityContext
from app.core.rbac import RBACManager, Role
from app.utils.prompts import TEXT2SQL_SYSTEM, TEXT2SQL_USER



def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )

async def safe_execute_query(db: AsyncSession, sql_query: str, params: list | dict) -> list[dict[str, Any]]:
    # Layer 4: Safe Execution Wrapper.
    # Normalizes parameters and prevents malformed execute calls.
    if isinstance(params, list):
        params = tuple(params)
        
    try:
        result = await db.execute(text(sql_query), params)
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
        return rows
    except Exception as e:
        logger.error(f"Execution Error: {e}")
        raise e

async def run_text2sql(question: str, db: AsyncSession, security_context: SecurityContext = None, domain: str = "general") -> list[dict[str, Any]]:
    """
    Convert *question* to a parameterised SQL query using the LLM,
    validate it, auto-repair if needed, execute it, and return results.
    Includes Independent Tool-Level Authorization.
    """
    if not security_context:
        logger.error("Text2SQL Tool blocked: Missing SecurityContext.")
        raise PermissionError("Independent Tool Authorization Failed: Missing SecurityContext.")
        
    try:
        user_role = Role(security_context.enterprise_role)
    except ValueError:
        user_role = Role.GUEST
        
    if domain != "general" and not RBACManager.can_access_domain(user_role, domain):
        logger.critical(f"Text2SQL Tool Authorization Violation! Role {user_role.value} attempted to query {domain}.")
        raise PermissionError(f"Independent Tool Authorization Failed: Role {user_role.value} cannot query {domain}.")

    schema_context = build_schema_metadata()
    system_prompt = TEXT2SQL_SYSTEM.format(schema=schema_context, domain=domain)

    llm = _build_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=TEXT2SQL_USER.format(question=question)),
    ]

    max_retries = 1
    
    for attempt in range(max_retries):
        try:
            response = await llm.ainvoke(messages)
            payload = json.loads(response.content)
            sql_query = payload.get("sql", "").strip()
            params = payload.get("params", [])
            explanation = payload.get("explanation", "")

            logger.debug(
                f"Text2SQL [Attempt {attempt+1}] | sql={sql_query!r} | params={params} | {explanation}"
            )

            # Layer 2: AST-based SQL Validation
            validation_error = validate_sql_against_schema(sql_query)
            
            if validation_error:
                logger.warning(f"Text2SQL validation failed: {validation_error}")
                # Layer 3: Auto-Repair via LLM Feedback
                messages.append(AIMessage(content=response.content))
                messages.append(HumanMessage(content=f"Your previous query was invalid. Fix this error and return corrected JSON: {validation_error}"))
                continue # Retry

            # Execution (Layer 4)
            rows = await safe_execute_query(db, sql_query, params)
            logger.info(f"Text2SQL returned {len(rows)} rows for: {question!r}")
            return rows

        except json.JSONDecodeError as exc:
            logger.error(f"Text2SQL: LLM returned non-JSON — {exc}")
            messages.append(AIMessage(content=response.content if 'response' in locals() else ""))
            messages.append(HumanMessage(content="You must return valid JSON only. Try again."))
        except Exception as exc:
            logger.error(f"Text2SQL execution failed: {exc}")
            return []
            
    logger.error("Text2SQL: Max retries reached. Query generation failed.")
    return []
