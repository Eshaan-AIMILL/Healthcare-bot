import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from app.db.models import Base
from sqlalchemy import MetaData
import logging

logger = logging.getLogger(__name__)

def validate_sql_against_schema(sql_query: str) -> str | None:
    """
    Validates a SQL query against the database schema using sqlglot.
    Returns an error string if validation fails, otherwise returns None.
    """
    metadata: MetaData = Base.metadata
    
    # 1. Parse the SQL query
    try:
        parsed = sqlglot.parse_one(sql_query, dialect="sqlite")
    except ParseError as e:
        return f"SQL syntax error: {e}"
        
    # 2. Extract tables and columns
    # Find all table names used in the query
    used_tables = set()
    table_aliases = {}
    for table in parsed.find_all(exp.Table):
        table_name = table.name
        alias = table.alias if table.alias else table_name
        used_tables.add(table_name)
        table_aliases[alias] = table_name

    # Validate tables exist in schema
    valid_tables = metadata.tables.keys()
    for table in used_tables:
        if table not in valid_tables:
            return f"Hallucinated table: '{table}'. Valid tables are: {', '.join(valid_tables)}"
            
    # Extract defined aliases so we don't flag them as hallucinated columns
    query_aliases = {alias.alias for alias in parsed.find_all(exp.Alias)}

    # 3. Extract and validate columns
    for column in parsed.find_all(exp.Column):
        col_name = column.name
        table_alias = column.table
        
        # Skip if it's an alias defined in the query
        if col_name in query_aliases:
            continue
        
        # If the column has a table alias, we can check it directly
        if table_alias:
            if table_alias not in table_aliases:
                return f"Unknown table alias '{table_alias}' for column '{col_name}'"
            real_table_name = table_aliases[table_alias]
            valid_columns = [c.name for c in metadata.tables[real_table_name].columns]
            if col_name not in valid_columns:
                return f"Hallucinated column: '{col_name}' in table '{real_table_name}'. Valid columns: {', '.join(valid_columns)}"
        else:
            # If no table alias, we must ensure it exists in at least one of the used tables
            found = False
            for t in used_tables:
                if col_name in [c.name for c in metadata.tables[t].columns]:
                    found = True
                    break
            if not found:
                # To be helpful to the LLM, let's list valid columns across the used tables
                valid_all = []
                for t in used_tables:
                    valid_all.extend([c.name for c in metadata.tables[t].columns])
                return f"Hallucinated column: '{col_name}'. It does not exist in any of the queried tables ({', '.join(used_tables)})."

    # 4. Check for forbidden operations
    if not isinstance(parsed, exp.Select):
        return "Only SELECT queries are allowed."
        
    return None # No errors found
