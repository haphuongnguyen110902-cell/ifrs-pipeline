"""
Week 4 script: create the database schema.

Goal: read your Neon connection string from .env, connect to the
online database, and run sql/schema.sql against it - this creates all
the empty tables (company, filing, period, ifrs_concept,
concept_mapping, fact_value) ready to be filled in.

Usage:
    python scripts/04_create_schema.py
"""
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv()  # reads .env in the current directory into environment variables
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not found. Check that your .env file exists and contains it.")
        sys.exit(1)

    schema_path = Path("sql/schema.sql")
    if not schema_path.exists():
        print(f"Schema file not found: {schema_path}")
        sys.exit(1)

    schema_sql = schema_path.read_text(encoding="utf-8")

    print("Connecting to database...")
    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.OperationalError as e:
        print(f"Could not connect to the database. Check your DATABASE_URL in .env.\nError: {e}")
        sys.exit(1)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
        print("Schema created successfully.")

        # verify: list the tables that now exist
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' ORDER BY table_name;
            """)
            tables = [row[0] for row in cur.fetchall()]
        print(f"Tables now in database: {tables}")
    finally:
        conn.close()
