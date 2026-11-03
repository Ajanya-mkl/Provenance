"""Evaluate the column mappers: python -m provenance.datasets.eval.run [--limit N] [--fresh].

Each case onboards a small synthetic fixture into a throwaway database in a
temporary folder (never the real warehouse), then maps it twice: with the
heuristic mapper (deterministic, always re-run) and with the agent (Groq, via
llm.py). Agent results are cached in eval/results/cases/<case_id>.json, keyed on
the fixture, model, prompt and tool schemas; --fresh ignores the cache. Writes
eval/results/report.json and docs/mapping_evaluation.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from provenance.datasets.mapping_agent import (
    SYSTEM_PROMPT,
    TOOL_SCHEMAS,
    MappingProposal,
    heuristic_proposal,
    propose_mapping,
)
from provenance.datasets.mapping_store import profile_facts
from provenance.datasets.onboard import PROJECT_ROOT, Paths, onboard
from provenance.datasets.validator import load_context, validate

EVAL_DIR = Path(__file__).resolve().parent
CASES_PATH = EVAL_DIR / "cases.json"
RESULTS_DIR = EVAL_DIR / "results"
DOC_PATH = PROJECT_ROOT / "docs" / "mapping_evaluation.md"
FIELDS = ("entity", "event_date", "outcome", "country", "mappable")

LIMITS = [
    "Seven cases is a smoke test, not a benchmark: the fixtures are small, synthetic and "
    "were written by the same people who wrote the heuristic's synonym lists, so the "
    "heuristic's score is optimistic.",
    "Every fixture is too small to score (under 50 rows per entity-quarter), so the "
    "scoreable verdict is not evaluated here; it is unit-tested with synthetic counts.",
    "The agent is non-deterministic; cached results reflect the model's behaviour when "
    "each case was first run. Re-run with --fresh to measure again.",
    "An earlier run, before the validator accepted late_when given as the column's own "
    "value (e.g. 'no') and confidence as a numeric string, had 4 rejections (one invalid "
    "JSON, one string confidence, two late_when='no'); every one was fixed on the retry.",
    "The agent never sees text values (only lengths), so it cannot tell a code from a "
    "name or a city from a country by content; the same holds for the heuristic.",
]


def load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _outcome_key(outcome: Any) -> Any:
    if not isinstance(outcome, dict) or not outcome.get("kind"):
        return None
    keys = {
        "late_flag": ("kind", "col", "late_when"),
        "days_pair": ("kind", "scheduled_col", "actual_col", "unit"),
        "delay_value": ("kind", "col", "unit"),
    }.get(outcome["kind"], ("kind",))
    return {
        key: str(outcome.get(key)).lower() if key == "late_when" else outcome.get(key)
        for key in keys
    }


def score(mapping: dict[str, Any] | None, mappable: bool | None, expected: dict) -> dict:
    """Per expected field: True when the mapper got it right."""
    mapping = mapping or {}
    marks: dict[str, bool] = {}
    for name in FIELDS:
        if name not in expected:
            continue
        want = expected[name]
        if name == "mappable":
            marks[name] = mappable is want
        elif name == "event_date":
            got = mapping.get("event_date") or {}
            marks[name] = (
                want is None and not got.get("col")
                if want is None
                else got.get("col") == want["col"] and got.get("format") == want["format"]
            )
        elif name == "outcome":
            marks[name] = _outcome_key(mapping.get("outcome")) in [_outcome_key(w) for w in want]
        else:
            marks[name] = mapping.get(name) == want
    return marks


def cache_key(case: dict[str, Any], model: str) -> str:
    fixture = (PROJECT_ROOT / case["fixture"]).read_bytes()
    material = json.dumps(
        [case, hashlib.sha256(fixture).hexdigest(), model, SYSTEM_PROMPT, TOOL_SCHEMAS],
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def summarize(proposal: MappingProposal) -> dict[str, Any]:
    first = proposal.attempts[0]["mapping"] if proposal.attempts else None
    if first is None and proposal.source == "agent":
        first = proposal.mapping
    return {
        "source": proposal.source,
        "mapping": proposal.mapping,
        "first_answer": first,
        "mappable": proposal.validation.mappable.ok,
        "mappable_reason": proposal.validation.mappable.reason,
        "scoreable": proposal.validation.scoreable.ok,
        "rejections": [attempt["errors"] for attempt in proposal.attempts],
        "fallback_reason": proposal.fallback_reason,
        "prompt_tokens": proposal.prompt_tokens,
        "completion_tokens": proposal.completion_tokens,
        "latency_s": proposal.latency_s,
        "steps": proposal.steps,
    }


def _first_mappable(first: dict[str, Any] | None, context) -> bool | None:
    if first is None:
        return None
    return validate(first, context, extra_facts=profile_facts(context)).mappable.ok


def run_case(case: dict[str, Any], workdir: Path, *, llm_factory, model: str, fresh: bool) -> dict:
    paths = Paths(workdir / "eval.duckdb", workdir / "datasets", workdir / "quarantine")
    result = onboard(PROJECT_ROOT / case["fixture"], case["case_id"], paths=paths)
    if result.status not in ("gated", "already_registered"):
        raise RuntimeError(f"{case['case_id']}: onboarding {result.status}: {result.problems}")
    context = load_context(paths.db_path, result.dataset_id)

    heuristic = summarize(heuristic_proposal(context))
    cache_path = RESULTS_DIR / "cases" / f"{case['case_id']}.json"
    key = cache_key(case, model)
    cached = None
    if not fresh and cache_path.exists():
        stored = json.loads(cache_path.read_text(encoding="utf-8"))
        cached = stored["agent"] if stored.get("key") == key else None
    if cached is None:
        agent = summarize(propose_mapping(context, llm_factory=llm_factory))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"key": key, "model": model, "agent": agent}, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        agent = cached
    expected = case["expected"]
    return {
        "case_id": case["case_id"],
        "description": case["description"],
        "cached": cached is not None,
        "heuristic": {
            **heuristic,
            "marks": score(heuristic["mapping"], heuristic["mappable"], expected),
        },
        "agent": {
            **agent,
            "marks": score(agent["mapping"], agent["mappable"], expected),
            "first_marks": score(
                agent["first_answer"], _first_mappable(agent["first_answer"], context), expected
            )
            if agent["first_answer"] is not None
            else None,
        },
    }


def _accuracy(results: list[dict], mapper: str, marks_key: str = "marks") -> dict[str, str]:
    table = {}
    for name in FIELDS:
        marks = [
            r[mapper][marks_key][name]
            for r in results
            if r[mapper].get(marks_key) and name in r[mapper][marks_key]
        ]
        table[name] = f"{sum(marks)}/{len(marks)}" if marks else "-"
    return table


def _tick(marks: dict[str, bool] | None, name: str) -> str:
    if not marks or name not in marks:
        return "-"
    return "yes" if marks[name] else "**no**"


def render(results: list[dict], model: str) -> str:
    heuristic, agent = _accuracy(results, "heuristic"), _accuracy(results, "agent")
    first = _accuracy(results, "agent", "first_marks")
    fallbacks = sum(r["agent"]["source"] == "heuristic" for r in results)
    rejections = sum(len(r["agent"]["rejections"]) for r in results)
    tokens = sum(r["agent"]["prompt_tokens"] + r["agent"]["completion_tokens"] for r in results)
    lines = [
        "# Column mapping evaluation",
        "",
        "Generated by `python -m provenance.datasets.eval.run`; do not edit by hand.",
        "",
        f"Model: `{model}`. Cases: {len(results)} small synthetic fixtures under "
        "`tests/fixtures/datasets/mapping/` plus two 10a fixtures. Each case is onboarded "
        "into a temporary database; the real warehouse is never touched.",
        "",
        "## Per-field accuracy",
        "",
        "| Field | Heuristic | Agent (after validation) | Agent first answer |",
        "|---|---|---|---|",
        *(f"| {name} | {heuristic[name]} | {agent[name]} | {first[name]} |" for name in FIELDS),
        "",
        f"Validator rejections of agent answers: {rejections}. Fallbacks to the heuristic: "
        f"{fallbacks} of {len(results)}. Agent tokens in total: {tokens}.",
        "",
        '"Agent (after validation)" is what the pipeline would store: the agent\'s answer if '
        "the validator accepted it (possibly after one retry), otherwise the heuristic's. "
        '"First answer" is the model\'s own first mapping before any validator feedback.',
        "",
        "## Cases",
        "",
        "| Case | Mapper | Source | entity | event_date | outcome | country | mappable "
        "| Rejections | Tokens | Latency (s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        for mapper in ("heuristic", "agent"):
            m = r[mapper]
            tok = m["prompt_tokens"] + m["completion_tokens"]
            lines.append(
                f"| {r['case_id']} | {mapper} | {m['source']} | "
                + " | ".join(_tick(m["marks"], name) for name in FIELDS)
                + f" | {len(m['rejections'])} | {tok or '-'} | {m['latency_s']} |"
            )
    notes = [
        f"- **{r['case_id']}** ({r['description']}): agent rejected: "
        + " / ".join("; ".join(errors) for errors in r["agent"]["rejections"])
        + (
            f". Fell back: {r['agent']['fallback_reason']}."
            if r["agent"]["fallback_reason"]
            else ""
        )
        for r in results
        if r["agent"]["rejections"] or r["agent"]["fallback_reason"]
    ]
    if notes:
        lines += ["", "## Validator rejections", "", *notes]
    lines += ["", "## Limits", "", *(f"- {limit}" for limit in LIMITS), ""]
    return "\n".join(lines)


def _llm_factory():
    from provenance.agent.llm import GroqLLM

    return GroqLLM.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--fresh", action="store_true", help="ignore cached agent results")
    args = parser.parse_args(argv)
    load_dotenv()
    model = os.environ.get("LLM_MODEL_NAME", "unknown")
    cases = load_cases()[: args.limit]
    results = []
    with tempfile.TemporaryDirectory(prefix="mapping_eval_") as tmp:
        for case in cases:
            workdir = Path(tmp) / case["case_id"]
            workdir.mkdir()
            outcome = run_case(
                case, workdir, llm_factory=_llm_factory, model=model, fresh=args.fresh
            )
            results.append(outcome)
            agent = outcome["agent"]
            print(
                f"{case['case_id']:<20} heuristic {sum(outcome['heuristic']['marks'].values())}"
                f"/{len(outcome['heuristic']['marks'])}  agent "
                f"{sum(agent['marks'].values())}/{len(agent['marks'])} ({agent['source']}"
                f"{', cached' if outcome['cached'] else ''})"
            )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "report.json").write_text(
        json.dumps({"model": model, "results": results}, indent=2, default=str), encoding="utf-8"
    )
    if args.limit is None:
        DOC_PATH.write_text(render(results, model), encoding="utf-8")
        print(f"wrote {DOC_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
