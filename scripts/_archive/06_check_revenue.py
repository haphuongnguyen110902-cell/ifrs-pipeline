import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("""
    SELECT c.display_label, f.value, p.end_date
    FROM fact_value f
    JOIN ifrs_concept c ON f.concept_id = c.concept_id
    JOIN period p ON f.period_id = p.period_id
    WHERE c.normalized_name = 'revenue'
    ORDER BY p.end_date
""")
for row in cur.fetchall():
    print(row)
conn.close()