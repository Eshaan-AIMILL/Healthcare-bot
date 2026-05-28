import sqlglot
from sqlglot import exp

sql = "SELECT 'Billing' AS category UNION ALL SELECT 'Pharmacy' ORDER BY category"
parsed = sqlglot.parse_one(sql, dialect="sqlite")

aliases = {a.alias for a in parsed.find_all(exp.Alias)}
print("Aliases:", aliases)

for column in parsed.find_all(exp.Column):
    print("Column found:", column.name)
