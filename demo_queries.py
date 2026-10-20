import duckdb

con = duckdb.connect("data/provenance.duckdb", read_only=True)


def show(title, sql, find=None):
    input(f"\n>>> {title}  (press Enter)")
    df = con.execute(sql).df()
    if find:
        mask = df.astype(str).apply(lambda r: r.str.contains(find).any(), axis=1)
        df = df[mask]
    print(df.to_string(index=False))


show("1. Quality gate results", "select * from bronze.dq_results")
show("2. History of one supplier (SCD2)", "select * from silver.dim_supplier_scd2", find="cat_golf_gloves")
show("3. Explainable score for one supplier", "select * from gold.supplier_risk_score", find="cat_golf_balls")
show("4. Risk flags by quarter", "select * from gold.supplier_risk_flags order by quarter")
show("5. Events waiting for the agent", "select event_type, investigation_status, count(*) as n from agent.events group by 1,2")