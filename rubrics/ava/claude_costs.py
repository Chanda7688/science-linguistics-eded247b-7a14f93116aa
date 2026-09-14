#!/usr/bin/env python3
"""Collect minimal LLM cost metadata for short-lived GitHub artifacts.

Records intentionally exclude prompts, model responses, task names, and file paths.
Costs are reported by model clients/Harbor or, for direct Anthropic calls, calculated
from the published standard Anthropic token rates below.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
SAFE_METADATA_FIELDS = {
    "duration_api_ms",
    "duration_ms",
    "duration_seconds",
    "num_turns",
    "pricing_basis",
    "return_code",
    "trial",
    "trial_count",
    "turn",
}

# Standard global API pricing in USD per million tokens as of 2026-07-23.
# Claude Code and Harbor provide their own estimate; this table is only used
# for direct Messages API calls, which return usage but not a dollar amount.
# https://platform.claude.com/docs/en/about-claude/pricing
MODEL_PRICES_PER_MTOK = {
    "claude-opus-4-8": {
        "input_tokens": 5.0,
        "output_tokens": 25.0,
        "cache_creation_input_tokens": 6.25,
        "cache_read_input_tokens": 0.50,
    },
    "claude-opus-4-7": {
        "input_tokens": 5.0,
        "output_tokens": 25.0,
        "cache_creation_input_tokens": 6.25,
        "cache_read_input_tokens": 0.50,
    },
    "claude-opus-4-6": {
        "input_tokens": 5.0,
        "output_tokens": 25.0,
        "cache_creation_input_tokens": 6.25,
        "cache_read_input_tokens": 0.50,
    },
    "claude-sonnet-4-6": {
        "input_tokens": 3.0,
        "output_tokens": 15.0,
        "cache_creation_input_tokens": 3.75,
        "cache_read_input_tokens": 0.30,
    },
    "claude-haiku-4-5": {
        "input_tokens": 1.0,
        "output_tokens": 5.0,
        "cache_creation_input_tokens": 1.25,
        "cache_read_input_tokens": 0.10,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None or value == "" or str(value).lower() == "null":
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return None


def _provider(model: str | None, explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip().lower()
    normalized = (model or "").strip().lower()
    if not normalized:
        return None
    if "/" in normalized:
        return normalized.split("/", 1)[0]
    if normalized.startswith("claude-"):
        return "anthropic"
    return None


def _clean_usage(usage: Mapping[str, Any] | Any | None) -> dict[str, int]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif not isinstance(usage, Mapping):
        usage = {
            field: getattr(usage, field, None)
            for field in TOKEN_FIELDS
        }
    clean: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        value = _number(usage.get(field), integer=True)
        if value is not None:
            clean[field] = int(value)
    return clean


def _github_event(environ: Mapping[str, str]) -> Mapping[str, Any]:
    event_path = environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def github_context(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    fields = {
        "repository": "GITHUB_REPOSITORY",
        "workflow": "GITHUB_WORKFLOW",
        "run_id": "GITHUB_RUN_ID",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "job": "GITHUB_JOB",
        "event_name": "GITHUB_EVENT_NAME",
        "trigger_sha": "GITHUB_SHA",
    }
    context = {
        output: str(env[source])
        for output, source in fields.items()
        if env.get(source)
    }
    event = _github_event(env)
    pull_request = (
        event.get("pull_request")
        if isinstance(event.get("pull_request"), Mapping)
        else {}
    )
    head = (
        pull_request.get("head")
        if isinstance(pull_request.get("head"), Mapping)
        else {}
    )
    base = (
        pull_request.get("base")
        if isinstance(pull_request.get("base"), Mapping)
        else {}
    )

    pr_number = env.get("PR_NUMBER") or event.get("number") or pull_request.get("number")
    head_sha = env.get("PR_HEAD_SHA") or head.get("sha") or env.get("GITHUB_SHA")
    base_sha = env.get("PR_BASE_SHA") or base.get("sha")
    if pr_number is not None and str(pr_number):
        context["pr_number"] = str(pr_number)
    if head_sha:
        context["head_sha"] = str(head_sha)
    if base_sha:
        context["base_sha"] = str(base_sha)
    return context


def make_record(
    *,
    source: str,
    stage: str,
    model: str | None = None,
    provider: str | None = None,
    estimated_cost_usd: float | str | None = None,
    session_count: int = 1,
    status: str = "success",
    usage: Mapping[str, Any] | Any | None = None,
    metadata: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": _now(),
        "source": source,
        "stage": stage,
        "status": status,
        "estimated_cost_usd": _number(estimated_cost_usd),
        "session_count": max(int(_number(session_count, integer=True) or 0), 0),
        "context": github_context(environ),
    }
    if model:
        record["model"] = str(model)
    resolved_provider = _provider(model, provider)
    if resolved_provider:
        record["provider"] = resolved_provider
    cleaned_usage = _clean_usage(usage)
    if cleaned_usage:
        record["usage"] = cleaned_usage
    if metadata:
        record["metadata"] = {
            str(key): value
            for key, value in metadata.items()
            if key in SAFE_METADATA_FIELDS and value is not None
        }
        if not record["metadata"]:
            del record["metadata"]
    return record


def write_record(output_dir: str | Path, record: Mapping[str, Any]) -> Path:
    """Write one record per file so concurrent jobs never contend on JSONL."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(record.get("stage", "unknown")))
    path = directory / f"{safe_stage}-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(dict(record), sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_records(root: str | Path) -> list[dict[str, Any]]:
    path = Path(root)
    files = [path] if path.is_file() else sorted(path.rglob("*.json"))
    records: list[dict[str, Any]] = []
    for file in files:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("schema_version") == SCHEMA_VERSION
            and payload.get("source")
            and payload.get("stage")
        ):
            records.append(payload)
    return records


def build_cli_record(
    payload: Mapping[str, Any],
    *,
    stage: str,
    model: str | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    status = "error" if payload.get("is_error") else str(payload.get("subtype") or "success")
    return make_record(
        source="claude_cli",
        stage=stage,
        model=model,
        estimated_cost_usd=payload.get("total_cost_usd"),
        status=status,
        usage=payload.get("usage"),
        metadata={
            "num_turns": _number(payload.get("num_turns"), integer=True),
            "duration_ms": _number(payload.get("duration_ms"), integer=True),
            "duration_api_ms": _number(payload.get("duration_api_ms"), integer=True),
        },
        environ=environ,
    )


def _normalized_model(model: str) -> str:
    return model.removeprefix("anthropic/")


def estimate_anthropic_cost(
    model: str,
    usage: Mapping[str, Any] | Any,
) -> float | None:
    prices = MODEL_PRICES_PER_MTOK.get(_normalized_model(model))
    if not prices:
        return None
    clean = _clean_usage(usage)
    return sum(clean.get(field, 0) * price for field, price in prices.items()) / 1_000_000


def record_anthropic_usage(
    output_dir: str | Path,
    *,
    stage: str,
    model: str,
    usage: Mapping[str, Any] | Any,
    status: str = "success",
    metadata: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    return write_record(
        output_dir,
        make_record(
            source="anthropic_messages",
            stage=stage,
            model=model,
            estimated_cost_usd=estimate_anthropic_cost(model, usage),
            status=status,
            usage=usage,
            metadata={
                **(dict(metadata) if metadata else {}),
                "pricing_basis": "standard_global_api_2026-07-23",
            },
            environ=environ,
        ),
    )


def extract_harbor_analysis_records(
    paths: Iterable[str | Path],
    *,
    stage: str,
    model: str | None,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path_like in paths:
        path = Path(path_like)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("trials"), list):
            continue
        cost = _number(payload.get("estimated_total_cost_usd"))
        if cost is None:
            trial_costs = [
                _number(trial.get("estimated_cost_usd"))
                for trial in payload["trials"]
                if isinstance(trial, dict)
            ]
            present = [value for value in trial_costs if value is not None]
            cost = sum(present) if present else None
        records.append(
            make_record(
                source="harbor_analyze",
                stage=stage,
                model=model,
                estimated_cost_usd=cost,
                session_count=len(payload["trials"]) + 1,
                status="success" if cost is not None else "cost_unavailable",
                metadata={"trial_count": len(payload["trials"])},
                environ=environ,
            )
        )
    return records


def _harbor_identity(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    def text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    agent_info = payload.get("agent_info") if isinstance(payload.get("agent_info"), dict) else {}
    model_info = (
        agent_info.get("model_info")
        if isinstance(agent_info.get("model_info"), dict)
        else {}
    )
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    config_agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    agent = next(
        (
            value
            for value in (
                text(payload.get("agent")),
                text(agent_info.get("name")),
                text(config_agent.get("name")),
            )
            if value
        ),
        "",
    )
    model = next(
        (
            value
            for value in (
                text(payload.get("model")),
                text(model_info.get("name")),
                text(config_agent.get("model_name")),
            )
            if value
        ),
        "",
    )
    provider = text(model_info.get("provider"))
    if provider and model and not model.startswith(f"{provider}/"):
        model = f"{provider}/{model}"
    return agent, model, provider


def _harbor_contexts(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if isinstance(payload.get("agent_result"), dict):
        return [payload["agent_result"]]
    contexts: list[Mapping[str, Any]] = []
    for step in payload.get("step_results") or []:
        if isinstance(step, dict) and isinstance(step.get("agent_result"), dict):
            contexts.append(step["agent_result"])
    return contexts


def extract_harbor_trial_records(
    paths: Iterable[str | Path],
    *,
    stage: str,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path_like in paths:
        path = Path(path_like)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        agent, model, provider = _harbor_identity(payload)
        contexts = _harbor_contexts(payload)
        fallback_cost = _number(payload.get("cost_usd"))
        fallback_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        has_identity = any((agent, model, provider))
        is_trial_result = bool(payload.get("trial_name")) and (has_identity or bool(contexts))
        is_manual_summary = payload.get("trial") is not None and has_identity
        if not is_trial_result and not is_manual_summary:
            continue

        if contexts:
            costs = [_number(context.get("cost_usd")) for context in contexts]
            present_costs = [cost for cost in costs if cost is not None]
            cost = sum(present_costs) if present_costs else None
            usage = {
                "input_tokens": sum(
                    int(_number(context.get("n_input_tokens"), integer=True) or 0)
                    for context in contexts
                ),
                "output_tokens": sum(
                    int(_number(context.get("n_output_tokens"), integer=True) or 0)
                    for context in contexts
                ),
                "cache_read_input_tokens": sum(
                    int(_number(context.get("n_cache_tokens"), integer=True) or 0)
                    for context in contexts
                ),
            }
        else:
            cost = fallback_cost
            usage = fallback_usage

        metadata = {
            "trial": _number(payload.get("trial"), integer=True),
            "duration_seconds": _number(
                payload.get("duration_secs") or payload.get("duration_seconds")
            ),
        }
        records.append(
            make_record(
                source="harbor_trial",
                stage=stage,
                model=model or None,
                provider=provider or None,
                estimated_cost_usd=cost,
                status="success" if cost is not None else "cost_unavailable",
                usage=usage,
                metadata=metadata,
                environ=environ,
            )
        )
    return records


def aggregate_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records_list = list(records)
    by_stage: dict[str, float] = defaultdict(float)
    by_model: dict[str, float] = defaultdict(float)
    by_provider: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)
    sessions_by_stage: dict[str, int] = defaultdict(int)
    sessions_by_model: dict[str, int] = defaultdict(int)
    sessions_by_provider: dict[str, int] = defaultdict(int)
    sessions_by_source: dict[str, int] = defaultdict(int)
    tokens: dict[str, int] = defaultdict(int)
    records_with_cost = 0
    session_count = 0
    total = 0.0

    for record in records_list:
        sessions = int(_number(record.get("session_count"), integer=True) or 0)
        session_count += sessions
        sessions_by_stage[str(record.get("stage", "unknown"))] += sessions
        sessions_by_model[str(record.get("model", "unknown"))] += sessions
        sessions_by_provider[str(record.get("provider", "unknown"))] += sessions
        sessions_by_source[str(record.get("source", "unknown"))] += sessions
        cost = _number(record.get("estimated_cost_usd"))
        if cost is not None:
            records_with_cost += 1
            total += cost
            by_stage[str(record.get("stage", "unknown"))] += cost
            by_model[str(record.get("model", "unknown"))] += cost
            by_provider[str(record.get("provider", "unknown"))] += cost
            by_source[str(record.get("source", "unknown"))] += cost
        usage = record.get("usage")
        if isinstance(usage, Mapping):
            for field in TOKEN_FIELDS:
                value = _number(usage.get(field), integer=True)
                if value is not None:
                    tokens[field] += int(value)

    rounded = lambda values: {key: round(value, 8) for key, value in sorted(values.items())}
    counted = lambda values: dict(sorted(values.items()))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "record_count": len(records_list),
        "estimated_session_or_request_count": session_count,
        "records_with_cost": records_with_cost,
        "records_without_cost": len(records_list) - records_with_cost,
        "total_estimated_cost_usd": round(total, 8),
        "cost_by_stage": rounded(by_stage),
        "cost_by_model": rounded(by_model),
        "cost_by_provider": rounded(by_provider),
        "cost_by_source": rounded(by_source),
        "sessions_or_requests_by_stage": counted(sessions_by_stage),
        "sessions_or_requests_by_model": counted(sessions_by_model),
        "sessions_or_requests_by_provider": counted(sessions_by_provider),
        "sessions_or_requests_by_source": counted(sessions_by_source),
        "token_usage": dict(sorted(tokens.items())),
        "notice": (
            "Client/provider-reported estimates only. Direct Anthropic Messages API costs "
            "use standard global list prices dated 2026-07-23."
        ),
    }


def write_report(input_root: str | Path, output_dir: str | Path) -> dict[str, Any]:
    records = sorted(
        load_records(input_root),
        key=lambda record: (
            str(record.get("stage", "")),
            str(record.get("recorded_at", "")),
            str(record.get("model", "")),
        ),
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = aggregate_records(records)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output / "records.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    (output / "README.md").write_text(
        "# LLM usage and cost estimates\n\n"
        "This artifact contains cost metadata only. It intentionally excludes prompts, "
        "model responses, task names, and source file paths.\n\n"
        "- `summary.json`: estimated totals grouped by workflow stage, provider, model, "
        "and source.\n"
        "- `records.jsonl`: one minimal record per captured model session/request or "
        "Harbor job.\n\n"
        "Raw model session IDs are deliberately omitted. Harbor analysis records can "
        "represent multiple analyzer sessions, reflected by `session_count`.\n\n"
        "The legacy `claude-costs-*` artifact name is retained for downstream compatibility. "
        "These are client/provider-reported estimates, not a reconciled provider invoice.\n",
        encoding="utf-8",
    )
    return summary


def _infer_model(command: list[str]) -> str | None:
    for index, value in enumerate(command):
        if value == "--model" and index + 1 < len(command):
            return command[index + 1]
        if value.startswith("--model="):
            return value.split("=", 1)[1]
    return None


def _run_cli(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("run-cli requires a command after --", file=sys.stderr)
        return 2
    if "--output-format" not in command and not any(
        value.startswith("--output-format=") for value in command
    ):
        command += ["--output-format", "json"]

    prompt = sys.stdin.buffer.read()
    try:
        completed = subprocess.run(command, input=prompt, capture_output=True)
    except FileNotFoundError as error:
        print(f"Claude command not found: {error}", file=sys.stderr)
        return 127
    except OSError as error:
        print(f"Claude command failed to start: {error}", file=sys.stderr)
        return 1
    if completed.stderr:
        sys.stderr.buffer.write(completed.stderr)
    raw_stdout = completed.stdout.decode("utf-8", errors="replace")
    model = args.model or _infer_model(command)

    try:
        payload = json.loads(raw_stdout)
        if not isinstance(payload, dict):
            raise ValueError("Claude JSON result was not an object")
    except (json.JSONDecodeError, ValueError) as error:
        try:
            write_record(
                args.cost_dir,
                make_record(
                    source="claude_cli",
                    stage=args.stage,
                    model=model,
                    status="unparseable_result",
                    metadata={"return_code": completed.returncode},
                ),
            )
        except Exception as write_error:
            print(f"warning: could not write Claude cost record: {write_error}", file=sys.stderr)
        sys.stdout.write(raw_stdout)
        print(f"Claude cost wrapper could not parse JSON output: {error}", file=sys.stderr)
        return completed.returncode or 1

    try:
        write_record(
            args.cost_dir,
            build_cli_record(payload, stage=args.stage, model=model),
        )
    except Exception as error:
        print(f"warning: could not write Claude cost record: {error}", file=sys.stderr)

    result = payload.get("result", "")
    sys.stdout.write(result if isinstance(result, str) else json.dumps(result))
    if result and not str(result).endswith("\n"):
        sys.stdout.write("\n")
    return completed.returncode


def _expand_json_paths(inputs: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            paths.append(path)
    return paths


def _extract_analysis_cli(args: argparse.Namespace) -> int:
    for record in extract_harbor_analysis_records(
        _expand_json_paths(args.inputs),
        stage=args.stage,
        model=args.model,
    ):
        write_record(args.cost_dir, record)
    return 0


def _extract_trials_cli(args: argparse.Namespace) -> int:
    for record in extract_harbor_trial_records(
        _expand_json_paths(args.inputs),
        stage=args.stage,
    ):
        write_record(args.cost_dir, record)
    return 0


def _aggregate_cli(args: argparse.Namespace) -> int:
    write_report(args.input, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    run_cli = sub.add_parser("run-cli", help="Run claude -p with JSON output and record cost")
    run_cli.add_argument("--stage", required=True)
    run_cli.add_argument("--cost-dir", required=True)
    run_cli.add_argument("--model", default="")
    run_cli.add_argument("command", nargs=argparse.REMAINDER)
    run_cli.set_defaults(func=_run_cli)

    analysis = sub.add_parser(
        "extract-harbor-analysis",
        help="Extract aggregate cost from Harbor analyze output",
    )
    analysis.add_argument("--stage", required=True)
    analysis.add_argument("--cost-dir", required=True)
    analysis.add_argument("--model", default="")
    analysis.add_argument("inputs", nargs="+")
    analysis.set_defaults(func=_extract_analysis_cli)

    trials = sub.add_parser(
        "extract-harbor-trials",
        help="Extract Claude agent cost from Harbor trial results",
    )
    trials.add_argument("--stage", required=True)
    trials.add_argument("--cost-dir", required=True)
    trials.add_argument("inputs", nargs="+")
    trials.set_defaults(func=_extract_trials_cli)

    aggregate = sub.add_parser("aggregate", help="Build summary.json and records.jsonl")
    aggregate.add_argument("--input", required=True)
    aggregate.add_argument("--output", required=True)
    aggregate.set_defaults(func=_aggregate_cli)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as error:  # Cost tracking must never become a review gate.
        print(f"warning: Claude cost tracking failed: {error}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
