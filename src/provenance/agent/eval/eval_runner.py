"""Evaluate the agent: python -m provenance.agent.eval.eval_runner [--limit N] [--model NAME].

Runs every case in cases.json against a throwaway COPY of the warehouse, so the
real agent tables are never written. Each case result is cached in
eval/results/cases/ keyed by the case, model, prompt and tool schemas; re-runs
reuse the cache (replaying it into the copy so memory still works) unless
--fresh is given. Writes eval/results/report.json and docs/agent_evaluation.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

from provenance.agent.approval import ApprovalRequired, apply
from provenance.agent.grounding import GroundingResult
from provenance.agent.llm import DEFAULT_MODEL, GroqLLM, LLMError
from provenance.agent.loop import MAX_STEPS, SYSTEM_PROMPT, InvestigationResult
from provenance.agent.run import record_outcome, run_investigation
from provenance.agent.store import AGENT_TABLES, ALLOWED_ACTIONS, table_exists
from provenance.agent.tools.investigation_tools import TOOL_SCHEMAS
from provenance.risk_engine.scorer import DEFAULT_DB_PATH, PROJECT_ROOT

EVAL_DIR = Path(__file__).resolve().parent
CASES_PATH = EVAL_DIR / "cases.json"
RESULTS_DIR = EVAL_DIR / "results"
DOC_PATH = PROJECT_ROOT / "docs" / "agent_evaluation.md"
DEFAULT_SLEEP_SECONDS = 15.0
EVAL_MAX_ATTEMPTS = 5
INSUFFICIENT = "insufficient data"
EDGE_KINDS = ("edge_unknown_supplier", "edge_unknown_quarter")

LIMITS = (
    "Ground truth is derived from the pipeline itself: the expected top factor is the "
    "scorer's own `top_contributing_factor`, so this measures whether the agent reports "
    "the pipeline faithfully, not whether the pipeline is right.",
    "The DataCo shipments are a public benchmark dataset that is likely synthetic; "
    "suppliers are product categories, not real companies.",
    "The sample is small (18 cases, one run each at temperature 0) and drawn from the "
    "20 current flags, so rates move by 5-7 points per case and are not significant.",
    "Only one current flag is low-confidence; the second low-confidence case and the two "
    "edge cases are synthetic events inserted into the evaluation copy.",
    "Grounding is a numeric check: it proves every number came from a tool result, not "
    "that the sentence around it is a correct reading of that number.",
    "Results are cached; a cached case reflects the model's behaviour when it was first run.",
)


def load_cases(path: str | Path = CASES_PATH, limit: int | None = None) -> list[dict[str, Any]]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))["cases"]
    return cases[:limit] if limit else cases


def prepare_copy(source_db: str | Path, workdir: Path, cases: Sequence[dict[str, Any]]) -> Path:
    """Copy the warehouse, clear the agent's own tables and add synthetic events."""
    copy_path = workdir / "eval.duckdb"
    shutil.copy2(source_db, copy_path)
    wal = Path(f"{source_db}.wal")
    if wal.exists():
        shutil.copy2(wal, Path(f"{copy_path}.wal"))
    with duckdb.connect(str(copy_path)) as connection:
        for table in AGENT_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS agent.{table}")
        for case in cases:
            synthetic = case.get("synthetic_event")
            if not synthetic:
                continue
            connection.execute(
                """
                INSERT INTO agent.events
                    (event_id, event_type, source, batch_id, created_at, payload,
                     investigation_status)
                VALUES (?, 'risk_flag', 'eval', 'eval', now(), ?, 'queued')
                """,
                [case["event_id"], json.dumps(synthetic["payload"])],
            )
    return copy_path


def cache_key(case: dict[str, Any], model: str, max_steps: int) -> str:
    material = json.dumps(
        [case, model, max_steps, SYSTEM_PROMPT, TOOL_SCHEMAS], sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cache_path(results_dir: Path, case: dict[str, Any]) -> Path:
    return results_dir / "cases" / f"{case['case_id']}.json"


def _load_cached(path: Path, key: str) -> dict[str, Any] | None:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cached if cached.get("key") == key else None


def _restore(cached: dict[str, Any]) -> tuple[InvestigationResult, GroundingResult]:
    return InvestigationResult(**cached["result"]), GroundingResult(**cached["grounding"])


def score_case(
    case: dict[str, Any],
    result: InvestigationResult | None,
    grounding: GroundingResult | None,
) -> dict[str, Any]:
    """Judge one case against its expectations. Pure; no I/O."""
    report = result.report if result else None
    summary = str(report.get("summary", "")).lower() if report else ""
    action = report.get("proposed_action") if report else None
    got_factor = str(report.get("top_factor", "")).strip().lower() if report else None
    expected = case.get("expected_top_factor")

    recalled = [
        call["result"]
        for call in (result.tool_calls if result else [])
        if call["name"] == "recall_past_investigations" and "error" not in call["result"]
    ]
    recalled_quarters = {
        past["quarter"].lower()
        for recall in recalled
        for past in recall.get("past_investigations", [])
        if past.get("quarter")
    }
    says_insufficient = INSUFFICIENT in summary
    handled = bool(report) and (says_insufficient or action == "no_action")
    memory_ok = bool(recalled_quarters) and any(q in summary for q in recalled_quarters)

    score = {
        "case_id": case["case_id"],
        "kind": case["kind"],
        "note": case.get("note"),
        "expected_top_factor": expected,
        "top_factor": got_factor,
        "top_factor_correct": (got_factor == expected) if expected else None,
        "grounded": bool(grounding and grounding.grounded),
        "grounded_ratio": grounding.grounded_ratio if grounding else 0.0,
        "ungrounded_numbers": grounding.ungrounded_numbers if grounding else [],
        "unknown_evidence_tools": grounding.unknown_evidence_tools if grounding else [],
        "proposed_action": action,
        "valid_action": action in ALLOWED_ACTIONS,
        "says_insufficient_data": says_insufficient,
        "insufficient_data_handled": handled if case.get("expect_insufficient_data") else None,
        "recalled_cases": len(recalled_quarters),
        "memory_referenced": memory_ok if case.get("expect_recall") else None,
        "steps": result.steps if result else 0,
        "tokens": (result.prompt_tokens + result.completion_tokens) if result else 0,
        "stop_reason": result.stop_reason if result else "error",
    }
    score["failures"] = _failures(case, score)
    return score


def _failures(case: dict[str, Any], score: dict[str, Any]) -> list[str]:
    problems = []
    if score["stop_reason"] == "error":
        return ["the model call failed after retries"]
    if score["stop_reason"] == "max_steps":
        problems.append("no final report within the step limit")
    if score["top_factor_correct"] is False:
        problems.append(
            f"top factor {score['top_factor']!r}, expected {score['expected_top_factor']!r}"
        )
    if not score["grounded"] and score["stop_reason"] != "max_steps":
        if score["ungrounded_numbers"]:
            problems.append(f"ungrounded numbers {score['ungrounded_numbers']}")
        if score["unknown_evidence_tools"]:
            problems.append(
                "evidence cites tools with no successful result "
                f"{score['unknown_evidence_tools']}"
            )
    if not score["valid_action"] and score["stop_reason"] != "max_steps":
        problems.append(f"action {score['proposed_action']!r} is not allowed")
    if score["insufficient_data_handled"] is False:
        problems.append(
            f"expected 'insufficient data' or no_action, got {score['proposed_action']!r}"
        )
    if score["memory_referenced"] is False:
        problems.append(
            "did not cite the recalled case"
            if score["recalled_cases"]
            else "recall returned no earlier case"
        )
    return problems


def _rate(values: Sequence[bool | None]) -> tuple[float | None, int]:
    judged = [value for value in values if value is not None]
    return (round(sum(judged) / len(judged), 3) if judged else None), len(judged)


def compute_metrics(
    scores: Sequence[dict[str, Any]],
    latencies: Sequence[float],
    guardrail_violations: int,
) -> dict[str, Any]:
    ran = [score for score in scores if score["stop_reason"] != "error"]
    metrics: dict[str, Any] = {}
    for name, values in (
        ("top_factor_accuracy", [s["top_factor_correct"] for s in scores]),
        ("grounded_rate", [s["grounded"] for s in scores]),
        ("valid_action_rate", [s["valid_action"] for s in scores]),
        (
            "low_confidence_handled_rate",
            [s["insufficient_data_handled"] for s in scores if s["kind"] == "low_confidence"],
        ),
        (
            "edge_case_handled_rate",
            [s["insufficient_data_handled"] for s in scores if s["kind"] in EDGE_KINDS],
        ),
        ("memory_recall_rate", [s["memory_referenced"] for s in scores]),
    ):
        value, count = _rate(values)
        metrics[name] = {"value": value, "n": count}
    metrics["guardrail_violations"] = {"value": guardrail_violations, "n": len(scores)}
    for name, values in (
        ("avg_steps", [s["steps"] for s in ran]),
        ("avg_tokens", [s["tokens"] for s in ran]),
        ("avg_latency_s", list(latencies)),
    ):
        metrics[name] = {
            "value": round(sum(values) / len(values), 2) if values else None,
            "n": len(values),
        }
    return metrics


def guardrail_probe(copy_db: Path) -> int:
    """Try to apply every action without approval, with the env flag turned off.

    Returns how many actions took effect (must be 0).
    """
    with duckdb.connect(str(copy_db), read_only=True) as connection:
        action_ids = (
            [
                row[0]
                for row in connection.execute(
                    "SELECT action_id FROM agent.actions WHERE status = 'proposed'"
                ).fetchall()
            ]
            if table_exists(connection, "agent", "actions")
            else []
        )
    previous = os.environ.get("AGENT_REQUIRE_APPROVAL")
    os.environ["AGENT_REQUIRE_APPROVAL"] = "false"
    applied = 0
    try:
        for action_id in action_ids:
            try:
                apply(copy_db, action_id, "eval_guardrail_probe")
                applied += 1
            except ApprovalRequired:
                pass
    finally:
        if previous is None:
            os.environ.pop("AGENT_REQUIRE_APPROVAL", None)
        else:
            os.environ["AGENT_REQUIRE_APPROVAL"] = previous
    with duckdb.connect(str(copy_db), read_only=True) as connection:
        for table in ("watchlist", "review_requests"):
            if table_exists(connection, "agent", table):
                applied += connection.execute(f"SELECT count(*) FROM agent.{table}").fetchone()[0]
    return applied


def run_eval(
    cases: Sequence[dict[str, Any]],
    source_db: str | Path,
    llm_factory: Callable[[], Any],
    model: str,
    results_dir: Path = RESULTS_DIR,
    doc_path: Path | None = DOC_PATH,
    fresh: bool = False,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    max_steps: int = MAX_STEPS,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "cases").mkdir(exist_ok=True)
    llm = None
    scores, latencies, cached_count, live_count = [], [], 0, 0

    with tempfile.TemporaryDirectory(prefix="provenance_eval_") as workdir:
        copy_db = prepare_copy(source_db, Path(workdir), cases)
        for index, case in enumerate(cases, start=1):
            key = cache_key(case, model, max_steps)
            path = _cache_path(results_dir, case)
            cached = None if fresh else _load_cached(path, key)
            result = grounding = None
            if cached:
                result, grounding = _restore(cached)
                latency = cached["latency_s"]
                cached_count += 1
                source = "cache"
            else:
                if live_count and sleep_seconds > 0:
                    sleep(sleep_seconds)
                llm = llm or llm_factory()
                started = time.perf_counter()
                try:
                    result, grounding = run_investigation(case["event_id"], copy_db, llm, max_steps)
                except LLMError as error:
                    log(f"[{index}/{len(cases)}] {case['case_id']}: model error ({error})")
                latency = round(time.perf_counter() - started, 2)
                live_count += 1
                source = "live"
                if result is not None:
                    path.write_text(
                        json.dumps(
                            {
                                "key": key,
                                "case_id": case["case_id"],
                                "model": model,
                                "created_at": datetime.now(UTC).isoformat(),
                                "latency_s": latency,
                                "result": asdict(result),
                                "grounding": grounding.as_dict(),
                            },
                            indent=2,
                            default=str,
                        ),
                        encoding="utf-8",
                    )
            if result is not None:
                # Record into the copy so later cases can recall this one.
                record_outcome(copy_db, result, grounding, model, requires_approval=True)
                latencies.append(latency)
            score = score_case(case, result, grounding)
            score["source"] = source
            score["latency_s"] = latency if result is not None else None
            scores.append(score)
            status = "ok" if not score["failures"] else "; ".join(score["failures"])
            log(f"[{index}/{len(cases)}] {case['case_id']} ({source}): {status}")

        violations = guardrail_probe(copy_db)

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model,
        "max_steps": max_steps,
        "case_count": len(cases),
        "cached_cases": cached_count,
        "live_cases": live_count,
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12],
        "metrics": compute_metrics(scores, latencies, violations),
        "cases": scores,
        "limits": list(LIMITS),
    }
    (results_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    if doc_path is not None:
        doc_path.write_text(render_markdown(report), encoding="utf-8")
    return report


METRIC_NOTES = {
    "top_factor_accuracy": "report.top_factor equals the pipeline's top_contributing_factor",
    "grounded_rate": "every number in the report appears in a tool result",
    "valid_action_rate": "proposed_action is no_action, watchlist or request_review",
    "low_confidence_handled_rate": "low-confidence flag: says insufficient data or no_action",
    "edge_case_handled_rate": "unknown supplier / quarter: says insufficient data or no_action",
    "memory_recall_rate": "repeated supplier: summary cites a recalled quarter",
    "guardrail_violations": "actions that took effect without approval (must be 0)",
    "avg_steps": "model calls per investigation (max 6)",
    "avg_tokens": "prompt + completion tokens per investigation",
    "avg_latency_s": "wall-clock seconds per investigation, including retries",
}


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Agent evaluation",
        "",
        "Generated by `python -m provenance.agent.eval.eval_runner`; do not edit by hand.",
        "",
        f"- Date: {report['generated_at']}",
        f"- Model: `{report['model']}` (temperature 0, max {report['max_steps']} steps)",
        f"- Cases: {report['case_count']} ({report['live_cases']} run live, "
        f"{report['cached_cases']} from cache)",
        f"- System prompt sha256: `{report['prompt_sha256']}`",
        "- Database: a temporary copy of `data/provenance.duckdb`; the real agent tables "
        "are not written.",
        "",
        "## Results",
        "",
        "| Metric | Value | n | Measures |",
        "|---|---|---|---|",
    ]
    for name, note in METRIC_NOTES.items():
        lines.append(f"| {name} | {_fmt(metrics[name]['value'])} | {metrics[name]['n']} | {note} |")
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Kind | Expected factor | Reported factor | Grounded | Action | Steps | Tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['kind']} | {_fmt(case['expected_top_factor'])} | "
            f"{_fmt(case['top_factor'])} | {_fmt(case['grounded'])} | "
            f"{_fmt(case['proposed_action'])} | {case['steps']} | {case['tokens']} |"
        )
    failures = [case for case in report["cases"] if case["failures"]]
    lines += ["", "## Cases the agent got wrong", ""]
    if failures:
        for case in failures:
            note = f" Case note: {case['note']}" if case.get("note") else ""
            lines.append(f"- **{case['case_id']}**: {'; '.join(case['failures'])}.{note}")
    else:
        lines.append("None.")
    lines += ["", "## Limits of this test", ""]
    lines += [f"- {limit}" for limit in report["limits"]]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument("--model", default=None, help="override LLM_MODEL_NAME")
    parser.add_argument("--fresh", action="store_true", help="ignore cached case results")
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help="seconds to wait between live cases",
    )
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args(argv)

    load_dotenv()
    source_db = args.db_path or Path(os.environ.get("PROVENANCE_DUCKDB_PATH", DEFAULT_DB_PATH))
    model = args.model or os.environ.get("LLM_MODEL_NAME", "").strip() or DEFAULT_MODEL

    def factory() -> GroqLLM:
        llm = GroqLLM.from_env(max_attempts=EVAL_MAX_ATTEMPTS)
        llm.model = model
        return llm

    report = run_eval(
        load_cases(limit=args.limit),
        source_db,
        factory,
        model,
        fresh=args.fresh,
        sleep_seconds=args.sleep,
        max_steps=args.max_steps,
    )
    print(json.dumps(report["metrics"], indent=2))
    return 0 if report["metrics"]["guardrail_violations"]["value"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
