# import json
# import os
# from typing import Dict, Any, List

# from sqlalchemy import MetaData
# from app.db.models import Base

# def build_schema_metadata() -> str:
#     """
#     Builds a highly accurate, LLM-friendly schema representation directly from SQLAlchemy models.
#     This guarantees that the schema the LLM sees exactly matches the code.
#     """
#     metadata: MetaData = Base.metadata
    
#     schema_info = {
#         "tables": {}
#     }
    
#     for table_name, table in metadata.tables.items():
#         columns = {}
#         for column in table.columns:
#             columns[column.name] = {
#                 "type": str(column.type),
#                 "nullable": column.nullable,
#                 "primary_key": column.primary_key
#             }
#             if column.foreign_keys:
#                 fks = [f"{fk.column.table.name}.{fk.column.name}" for fk in column.foreign_keys]
#                 columns[column.name]["foreign_keys"] = fks
                
#         schema_info["tables"][table_name] = {
#             "columns": columns,
#         }
        
#     lines = []
#     lines.append("DATABASE SCHEMA (Source of Truth):")
#     lines.append("===================================")
    
#     for table_name, table_info in schema_info["tables"].items():
#         lines.append(f"\nTABLE: {table_name}")
#         for col_name, col_info in table_info["columns"].items():
#             pk_str = " (PRIMARY KEY)" if col_info.get("primary_key") else ""
#             fk_str = f" (FOREIGN KEY to {', '.join(col_info.get('foreign_keys', []))})" if col_info.get("foreign_keys") else ""
#             null_str = " (NULL)" if col_info.get("nullable") else " (NOT NULL)"
#             lines.append(f"  - {col_name} : {col_info['type']}{pk_str}{fk_str}{null_str}")
            
#     # Include some additional rules
#     lines.append("\nQUERY RULES (must follow every rule):")
#     lines.append("  - ALWAYS use SELECT only. Never INSERT, UPDATE, DELETE, or DROP.")
#     lines.append("  - ALWAYS use parameterised placeholders (?) for any user-supplied values.")
#     lines.append("  - For date ranges use: DATE('now', '-N days')")
#     lines.append("  - Do NOT hallucinate columns or tables. Only use what is strictly defined above.")
    
#     return "\n".join(lines)


"""
Dynamic Schema Introspection for Text2SQL
==========================================
Optimized for Small Language Models (7B parameter range, e.g. qwen2.5:7b).

Key changes vs. original:
1. Domain-aware schema pruning — only the tables relevant to the query domain
   are serialized into the prompt, eliminating noise that causes a 7B model
   to hallucinate columns from irrelevant tables.
2. Enum value injection — SQLAlchemy Column.info / column-level type info and
   model-level enum constants are extracted and written explicitly next to each
   column, so the model never has to *guess* valid discrete values.
3. Relationship hints — FK paths are rendered as a compact "JOIN HINT" line so
   the model can construct joins without memorizing the schema topology.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import MetaData
from app.db.models import Base


# ---------------------------------------------------------------------------
# Domain → table mapping
# A 7B model should NEVER see tables outside the active domain.
# Cross-domain queries should pass all relevant domains in a list.
# ---------------------------------------------------------------------------

DomainName = Literal["billing", "compliance", "pharmacy", "patient", "dispatch", "general"]

_DOMAIN_TABLES: dict[str, list[str]] = {
    "billing": [
        "patients",
        "encounters",
        "billing_claims",
    ],
    "compliance": [
        "clinical_processes",
        "audit_findings",
    ],
    "pharmacy": [
        "drugs",
        "drug_inventory",
    ],
    "patient": [
        "patients",
        "appointments",
        "patient_complaints",
    ],
    "dispatch": [
        "vehicles",
        "routes",
        "delivery_records",
    ],
    # 'general' and 'audit' (internal) get the full schema — used only as
    # a last resort or for cross-domain dashboards.
    "general": [],   # resolved to ALL tables below
}

# Explicit enum values that are not derivable from the SQLAlchemy Column type
# alone (they live in column comments / application logic).  Maintaining them
# here keeps a single source of truth and avoids regex-scraping model files.
_ENUM_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "patients": {
        "patient_tier": ["Standard", "Premium", "VIP"],
        "gender": ["Male", "Female", "Other"],
    },
    "encounters": {},
    "billing_claims": {
        "claim_status": ["Pending", "Approved", "Rejected", "UnderReview"],
        "error_type": ["CodeMismatch", "MissingModifier", "Unbundling", "Upcoding"],
        "has_coding_error": ["0 (False)", "1 (True)"],
    },
    "clinical_processes": {
        "patient_consent_obtained": ["0 (False)", "1 (True)"],
        "documentation_complete": ["0 (False)", "1 (True)"],
        "protocol_followed": ["0 (False)", "1 (True)"],
        "incident_reported": ["0 (False)", "1 (True)"],
    },
    "audit_findings": {
        "violation_type": [
            "DocumentationGap",
            "ConsentMissing",
            "ProtocolDeviation",
            "ReportingFailure",
        ],
        "severity": ["Critical", "Major", "Minor"],
        "regulation_body": ["HIPAA", "JCI", "SOP"],
        "resolution_status": ["Open", "InReview", "Resolved"],
    },
    "drugs": {
        "category": ["Antibiotic", "Analgesic", "Cardiac", "Oncology"],
        "unit": ["tablets", "vials", "ml", "mg"],
    },
    "drug_inventory": {},
    "appointments": {
        "appointment_category": ["GP", "Specialist", "Emergency"],
        "appointment_status": ["Scheduled", "Completed", "Cancelled", "NoShow"],
    },
    "patient_complaints": {
        "sla_breached": ["0 (False)", "1 (True)"],
        "breach_severity": ["Critical", "High", "Medium", "None"],
        "compensation_eligible": ["0 (False)", "1 (True)"],
        "compensation_type": ["Voucher", "Refund", "Escalation", "None"],
        "resolution_status": ["Open", "AutoResolved", "EscalatedToStaff"],
    },
    "vehicles": {
        "vehicle_type": ["Ambulance", "Van", "Motorcycle", "Refrigerated"],
        "available": ["0 (False)", "1 (True)"],
    },
    "routes": {
        "route_type": ["Highway", "Urban", "Rural"],
    },
    "delivery_records": {
        "delivery_category": ["Emergency", "Routine", "Refrigerated"],
        "delivery_status": ["Scheduled", "InTransit", "Delivered", "Delayed"],
        "sla_breached": ["0 (False)", "1 (True)"],
    },
    "audit_logs": {
        "status": ["Success", "Blocked", "Error"],
    },
}

# Human-readable JOIN hints keyed by the child (many) table.
_JOIN_HINTS: dict[str, str] = {
    "billing_claims": (
        "JOIN encounters ON billing_claims.encounter_id = encounters.encounter_id | "
        "JOIN patients ON billing_claims.patient_id = patients.patient_id"
    ),
    "encounters": "JOIN patients ON encounters.patient_id = patients.patient_id",
    "audit_findings": "JOIN clinical_processes ON audit_findings.process_id = clinical_processes.process_id",
    "drug_inventory": "JOIN drugs ON drug_inventory.drug_id = drugs.drug_id",
    "appointments": "JOIN patients ON appointments.patient_id = patients.patient_id",
    "patient_complaints": (
        "JOIN patients ON patient_complaints.patient_id = patients.patient_id | "
        "JOIN appointments ON patient_complaints.appointment_id = appointments.appointment_id"
    ),
    "delivery_records": (
        "JOIN vehicles ON delivery_records.vehicle_id = vehicles.vehicle_id | "
        "JOIN routes ON delivery_records.route_id = routes.route_id"
    ),
}


def _resolve_tables_for_domains(domains: list[str]) -> list[str]:
    """Return the deduplicated, ordered list of table names for *domains*."""
    if not domains or "general" in domains:
        # Full schema — only for cross-domain or fallback paths.
        metadata: MetaData = Base.metadata
        return list(metadata.tables.keys())

    seen: set[str] = set()
    ordered: list[str] = []
    for domain in domains:
        for tbl in _DOMAIN_TABLES.get(domain, []):
            if tbl not in seen:
                seen.add(tbl)
                ordered.append(tbl)
    return ordered


def _format_column(
    col_name: str,
    col_type: str,
    is_pk: bool,
    is_nullable: bool,
    fk_targets: list[str],
    enum_values: list[str] | None,
) -> str:
    """Render a single column as a compact, LLM-readable descriptor line."""
    parts: list[str] = [f"  - {col_name} : {col_type}"]

    flags: list[str] = []
    if is_pk:
        flags.append("PK")
    if fk_targets:
        flags.append(f"FK→{', '.join(fk_targets)}")
    if not is_nullable:
        flags.append("NOT NULL")
    if flags:
        parts.append(f"  [{', '.join(flags)}]")

    if enum_values:
        parts.append(f"  — VALUES: {' | '.join(enum_values)}")

    return "".join(parts)


def build_schema_metadata(
    domains: list[str] | str | None = None,
) -> str:
    """
    Build an LLM-ready schema string scoped to the requested *domains*.

    Parameters
    ----------
    domains:
        A domain name or list of domain names (``"billing"``, ``"compliance"``,
        etc.).  Pass ``None`` or ``"general"`` to include every table.

    Returns
    -------
    str
        A compact schema block ready to be injected into a system prompt.
    """
    if domains is None:
        domains = ["general"]
    if isinstance(domains, str):
        domains = [domains]

    target_tables = _resolve_tables_for_domains(domains)
    metadata: MetaData = Base.metadata

    lines: list[str] = [
        "## ACTIVE SCHEMA (use ONLY these tables and columns)",
        "=" * 50,
    ]

    for table_name in target_tables:
        table = metadata.tables.get(table_name)
        if table is None:
            continue  # Model not yet migrated; skip gracefully.

        lines.append(f"\nTABLE: {table_name}")

        for column in table.columns:
            fk_targets = [
                f"{fk.column.table.name}.{fk.column.name}"
                for fk in column.foreign_keys
            ]
            enum_vals = _ENUM_OVERRIDES.get(table_name, {}).get(column.name)
            lines.append(
                _format_column(
                    col_name=column.name,
                    col_type=str(column.type),
                    is_pk=bool(column.primary_key),
                    is_nullable=bool(column.nullable),
                    fk_targets=fk_targets,
                    enum_values=enum_vals,
                )
            )

        # Append a JOIN hint for tables that are typically joined.
        if table_name in _JOIN_HINTS:
            lines.append(f"  JOIN HINT: {_JOIN_HINTS[table_name]}")

    # ── Hard rules appended directly in the schema block ───────────────────
    lines += [
        "",
        "## MANDATORY QUERY RULES",
        "  1. SELECT only. NEVER INSERT / UPDATE / DELETE / DROP.",
        "  2. Use named SQLAlchemy placeholders (:param_name) for every user value.",
        "  3. Date arithmetic: DATE('now', '-N days') or DATE(:current_date, '+N days').",
        "  4. Do NOT invent columns. If a column is not listed above, it does not exist.",
        "  5. Boolean columns store 0/1 integers, not TRUE/FALSE keywords.",
    ]

    return "\n".join(lines)


def get_valid_columns_for_table(table_name: str) -> list[str]:
    """
    Return the exact column names for *table_name*.

    Used by the error-recovery layer in text2sql.py to build directive
    correction messages like:
        'Column X does not exist in Y. Valid columns: [A, B, C]'
    """
    metadata: MetaData = Base.metadata
    table = metadata.tables.get(table_name)
    if table is None:
        return []
    return [col.name for col in table.columns]


def get_all_table_names() -> list[str]:
    """Return every table name registered in Base.metadata."""
    return list(Base.metadata.tables.keys())