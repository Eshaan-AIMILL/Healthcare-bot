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
    # Find derived aliases from Subqueries and CTEs
    derived_aliases = set()
    for sub in parsed.find_all(exp.Subquery):
        if sub.alias:
            derived_aliases.add(sub.alias)
    for cte in parsed.find_all(exp.CTE):
        if cte.alias:
            derived_aliases.add(cte.alias)
            
    used_tables = set()
    table_aliases = {}
    
    # Find all table names used in the query
    for table in parsed.find_all(exp.Table):
        table_name = table.name
        alias = table.alias if table.alias else table_name
        
        # If the 'table' is actually a CTE name, treat its alias as derived
        if table_name in derived_aliases:
            derived_aliases.add(alias)
            continue
            
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
    columns_to_validate = []
    for column in parsed.find_all(exp.Column):
        columns_to_validate.append((column.name, column.table))
    for schema_node in parsed.find_all(exp.Schema):
        tbl_name = schema_node.this.name if hasattr(schema_node.this, "name") else ""
        if tbl_name:
            for expr in schema_node.expressions:
                if isinstance(expr, exp.Identifier):
                    columns_to_validate.append((expr.name, tbl_name))

    for col_name, table_alias in columns_to_validate:
        # Skip if it's an alias defined in the query
        if col_name in query_aliases:
            continue
            
        # Skip strict validation if the table alias is a derived subquery or CTE
        if table_alias and table_alias in derived_aliases:
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
    allowed_types = (exp.Select, exp.Insert, exp.Update, exp.Delete, exp.Union, exp.Except, exp.Intersect)
    if not isinstance(parsed, allowed_types):
        return "Only SELECT, INSERT, UPDATE, and DELETE queries are allowed."
        
    forbidden_classes = (exp.Drop, exp.Alter, exp.Create)
    for node in parsed.walk():
        if isinstance(node, forbidden_classes):
            return f"DDL operation '{type(node).__name__}' is strictly forbidden."
            
    # SQLite does not fully support RIGHT and FULL OUTER joins in older versions.
    # We catch this here so the LLM auto-repair logic can switch to LEFT joins.
    for node in parsed.find_all(exp.Join):
        if node.side in ("FULL", "RIGHT"):
            return f"Unsupported syntax: '{node.side} JOIN' is not supported in this version of SQLite. Please restructure your query using 'LEFT JOIN' instead."
        
    return None # No errors found
