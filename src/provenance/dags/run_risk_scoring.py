from dagster import AssetExecutionContext, AssetKey, MaterializeResult, MetadataValue, asset

from provenance.risk_engine.scorer import score_supplier_risk


@asset(
    name="supplier_risk_scoring",
    deps=[AssetKey(["gold", "mart_supplier_risk"])],
    group_name="gold",
)
def supplier_risk_scoring_asset(context: AssetExecutionContext) -> MaterializeResult:
    summary = score_supplier_risk()
    context.log.info(
        "Scored %s supplier histories through %s; emitted %s risk flags and %s events.",
        summary.scores_written,
        summary.quarter,
        summary.flags_emitted,
        summary.events_emitted,
    )
    return MaterializeResult(
        metadata={
            "quarter": MetadataValue.text(summary.quarter.isoformat()),
            "scores_written": MetadataValue.int(summary.scores_written),
            "flags_emitted": MetadataValue.int(summary.flags_emitted),
            "events_emitted": MetadataValue.int(summary.events_emitted),
        }
    )