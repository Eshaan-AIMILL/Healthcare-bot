import json
import os
from typing import Dict, Any, List

from sqlalchemy import MetaData
from app.db.models import Base

def build_schema_metadata() -> str:
    """
    Builds a highly accurate, LLM-friendly schema representation directly from SQLAlchemy models.
    This guarantees that the schema the LLM sees exactly matches the code.
    """
    metadata: MetaData = Base.metadata
    
    schema_info = {
        "tables": {}
    }
    
    for table_name, table in metadata.tables.items():
        columns = {}
        for column in table.columns:
            columns[column.name] = {
                "type": str(column.type),
                "nullable": column.nullable,
                "primary_key": column.primary_key
            }
            if column.foreign_keys:
                fks = [f"{fk.column.table.name}.{fk.column.name}" for fk in column.foreign_keys]
                columns[column.name]["foreign_keys"] = fks
                
        schema_info["tables"][table_name] = {
            "columns": columns,
        }
        
    lines = []
    lines.append("DATABASE SCHEMA (Source of Truth):")
    lines.append("===================================")
    
    for table_name, table_info in schema_info["tables"].items():
        lines.append(f"\nTABLE: {table_name}")
        for col_name, col_info in table_info["columns"].items():
            pk_str = " (PRIMARY KEY)" if col_info.get("primary_key") else ""
            fk_str = f" (FOREIGN KEY to {', '.join(col_info.get('foreign_keys', []))})" if col_info.get("foreign_keys") else ""
            null_str = " (NULL)" if col_info.get("nullable") else " (NOT NULL)"
            lines.append(f"  - {col_name} : {col_info['type']}{pk_str}{fk_str}{null_str}")
            
    # Include some additional rules
    lines.append("\nQUERY RULES (must follow every rule):")
    lines.append("  - ALWAYS use SELECT only. Never INSERT, UPDATE, DELETE, or DROP.")
    lines.append("  - ALWAYS use parameterised placeholders (?) for any user-supplied values.")
    lines.append("  - For date ranges use: DATE('now', '-N days')")
    lines.append("  - Do NOT hallucinate columns or tables. Only use what is strictly defined above.")
    
    return "\n".join(lines)
