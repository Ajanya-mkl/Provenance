# ruff: noqa: E501, BLE001
"""
Provenance live demo (read-only). Run from the project folder, venv active,
and with `dagster dev` STOPPED (DuckDB allows one writer):

    python demo_queries.py

Press Enter at each step. Nothing in the database is changed.
"""
import sys

import duckdb
import pandas as pd

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)

try:
    con = duckdb.connect("data/provenance.duckdb", read_only=True)
except Exception as e:
    print("Could not open the database:", e)
    print("Stop `dagster dev` (Ctrl+C) and run this from the project folder.")
    sys.exit(1)


def short(v, n=40):
    s = str(v)
    return s if len(s) <= n else s[: n - 3] + "..."


def show(title, sql, limit=25):
    input(f"\n>>> {title}   (press Enter)")
    try:
        df = con.execute(sql).df()
    except Exception as e:
        print(f"[skipped] {type(e).__name__}: {e}")
        return
    if df.empty:
        print("(no rows)")
        return
    df = df.apply(lambda col: col.map(lambda v: short(v) if isinstance(v, str) else v))
    print(df.head(limit).to_string(index=False))
    print(f"\n[{len(df)} row(s)]")


show("1. Quality gate results",
     "select * from bronze.dq_results")

show("2. History of one supplier (SCD Type 2)",
     """select supplier_key, supplier_name, delivery_band, left(row_hash, 8) as row_hash,
               effective_from, effective_to, is_current, version_number
        from silver.dim_supplier_scd2
        where supplier_key = 'cat_golf_gloves' order by version_number""")

show("3. Explainable score for one supplier (cat_golf_balls)",
     """select quarter, round(score, 3) as score, tier, confidence,
               round(json_extract_string(contributing_factors, '$.country_risk.weighted_contribution')::double, 3) as country_risk_part,
               round(json_extract_string(contributing_factors, '$.delay_stddev.weighted_contribution')::double, 3) as delay_part,
               round(json_extract_string(contributing_factors, '$.late_rate.weighted_contribution')::double, 3) as late_rate_part
        from gold.supplier_risk_score
        where supplier_key = 'cat_golf_balls' order by quarter""")

show("4. Risk flags by quarter",
     """select supplier_key, quarter, old_tier, new_tier, round(score, 3) as score,
               json_extract_string(top_contributing_factor, '$.name') as top_factor, confidence
        from gold.supplier_risk_flags
        where resolved_at is null order by quarter, supplier_key""")

show("5. Events waiting for the agent",
     """select event_type, investigation_status, count(*) as n
        from agent.events group by 1, 2 order by 1, 2""")

print("\nDemo finished.")