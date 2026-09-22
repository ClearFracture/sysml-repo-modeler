from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen


HELPER_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = HELPER_ROOT.parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "src" / "backend" / "sysml-backend"
sys.path.insert(0, str(BACKEND_ROOT))

from sysml_backend.services.architecture import (  # noqa: E402
    ArchitectureClassifier,
    ArchitectureClassifierConfig,
)
from sysml_backend.services.evidence import build_repository_evidence  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate architecture candidate extraction and repeated Jev decisions "
            "against a source-backed gold case."
        )
    )
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--jev-runs", type=int, default=10)
    parser.add_argument("--confidence-threshold", type=float)
    parser.add_argument("--inventory", action="append", type=Path, default=[])
    parser.add_argument(
        "--inventory-url",
        action="append",
        default=[],
        metavar="LABEL=URL",
        help="Score a captured architecture response without saving secrets or fixtures.",
    )
    parser.add_argument("--sysml", action="append", type=Path, default=[])
    parser.add_argument("--sysml-url", action="append", default=[], metavar="LABEL=URL")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--jev-input-cost-per-million",
        type=float,
        default=0.042,
        help="Used only for the estimated Jev cost in the report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.jev_runs < 0:
        raise SystemExit("--jev-runs must be zero or greater")

    case = _read_json(args.case)
    repo = args.repo.resolve()
    if not repo.is_dir():
        raise SystemExit(f"Repository does not exist: {repo}")
    repository_commit = _repository_commit(repo)
    expected_commit = str(case["repository"].get("commit") or "")
    if expected_commit and not repository_commit.startswith(expected_commit):
        raise SystemExit(
            f"Repository commit {repository_commit} does not match case commit "
            f"{expected_commit}"
        )

    package = _package(case, repo)
    started = time.perf_counter()
    evidence = build_repository_evidence(package, repo.parent)
    candidate_inventory = _classifier(case, None, args.confidence_threshold).classify(
        package, evidence
    )

    trials: list[dict[str, Any]] = []
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if args.jev_runs and not api_key:
        raise SystemExit(
            "TYPESAFE_API_KEY is required when --jev-runs is greater than 0"
        )
    for trial_number in range(1, args.jev_runs + 1):
        trial_started = time.perf_counter()
        inventory = _classifier(case, api_key, args.confidence_threshold).classify(
            package, evidence
        )
        elapsed = time.perf_counter() - trial_started
        trial = _score_trial(
            f"jev-{trial_number}", "jev", inventory, case["decisions"], elapsed
        )
        trial["classifierStatus"] = inventory.get("classifier", {}).get("status")
        _attach_resource_usage(trial, inventory)
        if trial["classifierStatus"] != "completed":
            trial["error"] = (
                f"Jev classifier status was {trial['classifierStatus'] or 'missing'}"
            )
        trials.append(trial)

    for inventory_path in args.inventory:
        inventory = _read_json(inventory_path)
        if "architecture" in inventory:
            inventory = inventory["architecture"]
        trial = _score_trial(
            inventory_path.stem,
            f"captured:{inventory_path.stem}",
            inventory,
            case["decisions"],
            None,
        )
        _attach_resource_usage(trial, inventory)
        trials.append(trial)

    for source in args.inventory_url:
        label, url = _labeled_url(source)
        inventory = _read_url_json(url)
        if "architecture" in inventory:
            inventory = inventory["architecture"]
        trial = _score_trial(
            label, f"captured:{label}", inventory, case["decisions"], None
        )
        _attach_resource_usage(trial, inventory)
        trials.append(trial)

    sysml_scores = [
        _score_sysml(path, path.read_text(encoding="utf-8"), case, repo)
        for path in args.sysml
    ]
    for source in args.sysml_url:
        label, url = _labeled_url(source)
        with urlopen(url, timeout=30) as response:  # noqa: S310
            content = response.read().decode("utf-8")
        sysml_scores.append(_score_sysml(Path(label), content, case, repo))
    report = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "case": {
            "id": case["id"],
            "repository": case["repository"],
            "repositoryPath": str(repo),
            "observedCommit": repository_commit,
        },
        "evidence": {
            "summary": evidence.get("summary", {}),
            "candidateScore": _score_candidates(candidate_inventory, case),
        },
        "decisionEvaluation": {
            "goldDecisionCount": len(case["decisions"]),
            "trials": trials,
            "bySystem": _aggregate_trials(
                trials, case["decisions"], args.jev_input_cost_per_million
            ),
        },
        "sysmlEvaluation": sysml_scores,
        "elapsedSeconds": round(time.perf_counter() - started, 6),
    }

    output = args.output or Path("dist") / "architecture-eval" / f"{case['id']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _print_summary(report, output)
    return 0 if all(trial.get("error") is None for trial in trials) else 1


def _classifier(
    case: dict[str, Any], api_key: str | None, threshold_override: float | None
) -> ArchitectureClassifier:
    classifier = case.get("classifier", {})
    return ArchitectureClassifier(
        ArchitectureClassifierConfig(
            api_key=api_key,
            base_url=os.environ.get(
                "TYPESAFE_BASE_URL",
                classifier.get("baseUrl", "https://api.typesafe.ai"),
            ),
            model=os.environ.get("JEV_MODEL", classifier.get("model", "jev-1.13.0")),
            confidence_threshold=(
                threshold_override
                if threshold_override is not None
                else float(classifier.get("confidenceThreshold", 0.8))
            ),
            timeout_seconds=float(os.environ.get("JEV_TIMEOUT_SECONDS", "30")),
        )
    )


def _package(case: dict[str, Any], repo: Path) -> dict[str, Any]:
    repository = case["repository"]
    return {
        "name": repository["name"],
        "repositories": [
            {
                "name": repository["name"],
                "path": str(repo),
                "role": repository.get("role", "application"),
                "url": repository.get("url", ""),
            }
        ],
    }


def _score_candidates(
    inventory: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    actual_components = {str(item["key"]) for item in inventory.get("components", [])}
    actual_dependencies = {
        str(item["key"]) for item in inventory.get("dependencies", [])
    }
    actual_identity = {
        _identity_key(item)
        for item in inventory.get("deduplication", {}).get("decisions", [])
    }
    expected = case["expectedCandidates"]
    groups = {
        "components": (actual_components, set(expected.get("components", []))),
        "dependencies": (actual_dependencies, set(expected.get("dependencies", []))),
        "identityPairs": (actual_identity, set(expected.get("identityPairs", []))),
    }
    result: dict[str, Any] = {}
    all_actual: set[str] = set()
    all_expected: set[str] = set()
    for name, (actual, expected_set) in groups.items():
        all_actual.update(actual)
        all_expected.update(expected_set)
        result[name] = _set_score(actual, expected_set)
    result["overall"] = _set_score(all_actual, all_expected)
    return result


def _set_score(actual: set[str], expected: set[str]) -> dict[str, Any]:
    matched = actual & expected
    return {
        "actualCount": len(actual),
        "expectedCount": len(expected),
        "matchedCount": len(matched),
        "precision": _ratio(len(matched), len(actual)),
        "recall": _ratio(len(matched), len(expected)),
        "missing": sorted(expected - actual),
        "unexpected": sorted(actual - expected),
    }


def _score_trial(
    name: str,
    system: str,
    inventory: dict[str, Any],
    gold: list[dict[str, Any]],
    latency_seconds: float | None,
) -> dict[str, Any]:
    decisions = _normalized_decisions(inventory)
    scored: list[dict[str, Any]] = []
    for expected in gold:
        actual = decisions.get(str(expected["key"]))
        expected_value = expected["expected"]
        accepted = bool(actual and actual["status"] == "accepted")
        value = actual.get("value") if actual else None
        gold_probability = _gold_probability(actual, expected_value)
        scored.append(
            {
                "key": expected["key"],
                "kind": expected["kind"],
                "expected": expected_value,
                "actual": value,
                "status": actual.get("status", "missing") if actual else "missing",
                "correct": accepted and value == expected_value,
                "confidence": actual.get("confidence") if actual else None,
                "goldProbability": gold_probability,
            }
        )

    accepted_items = [item for item in scored if item["status"] == "accepted"]
    correct_items = [item for item in accepted_items if item["correct"]]
    probabilities = [
        float(item["goldProbability"])
        for item in scored
        if item["goldProbability"] is not None
    ]
    status = inventory.get("classifier", {}).get("status")
    error = None
    if status == "error":
        error = str(inventory.get("classifier", {}).get("error") or "classifier error")
    return {
        "name": name,
        "system": system,
        "latencySeconds": round(latency_seconds, 6)
        if latency_seconds is not None
        else None,
        "acceptedCount": len(accepted_items),
        "abstainedCount": len(scored) - len(accepted_items),
        "correctAcceptedCount": len(correct_items),
        "acceptedAccuracy": _ratio(len(correct_items), len(accepted_items)),
        "coverage": _ratio(len(accepted_items), len(scored)),
        "selectiveScore": _ratio(len(correct_items), len(scored)),
        "meanGoldProbability": _mean(probabilities),
        "binaryBrierScore": _mean([(1.0 - value) ** 2 for value in probabilities]),
        "decisions": scored,
        "error": error,
    }


def _normalized_decisions(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for collection in ("components", "dependencies"):
        for item in inventory.get(collection, []):
            decision = item.get("decision", {})
            result[str(item["key"])] = {
                "value": item.get("classification"),
                "status": decision.get("status", "missing"),
                "confidence": decision.get("confidence"),
                "probabilities": decision.get("probabilities", {}),
            }
    for item in inventory.get("deduplication", {}).get("decisions", []):
        decision = item.get("decision", {})
        result[_identity_key(item)] = {
            "value": item.get("same"),
            "status": decision.get("status", "missing"),
            "confidence": decision.get("confidence"),
            "probabilitySame": decision.get("probabilitySame"),
        }
    return result


def _identity_key(item: dict[str, Any]) -> str:
    left, right = sorted((str(item.get("leftKey")), str(item.get("rightKey"))))
    return f"dependency_identity:{left}|{right}"


def _gold_probability(actual: dict[str, Any] | None, expected: Any) -> float | None:
    if actual is None:
        return None
    if "probabilitySame" in actual:
        value = actual.get("probabilitySame")
        if value is None:
            return None
        probability_same = float(value)
        return probability_same if expected is True else 1.0 - probability_same
    probabilities = actual.get("probabilities")
    if isinstance(probabilities, dict) and str(expected) in probabilities:
        return float(probabilities[str(expected)])
    return None


def _aggregate_trials(
    trials: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    jev_input_cost_per_million: float,
) -> dict[str, Any]:
    systems: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        systems.setdefault(str(trial["system"]), []).append(trial)
    result: dict[str, Any] = {}
    for system, items in systems.items():
        successful = [item for item in items if item.get("error") is None]
        latencies = [
            float(item["latencySeconds"])
            for item in successful
            if item.get("latencySeconds") is not None
        ]
        input_tokens = sum(
            _input_tokens(item.get("classifierUsage", {})) for item in items
        )
        fallback_tokens = sum(
            _total_tokens(item.get("fallbackUsage", {})) for item in items
        )
        fallback_cost = sum(
            _usage_cost(item.get("fallbackUsage", {})) for item in items
        )
        estimated_classifier_cost = (
            input_tokens / 1_000_000 * jev_input_cost_per_million
        )
        result[system] = {
            "trialCount": len(items),
            "successfulTrialCount": len(successful),
            "meanAcceptedAccuracy": _mean(
                [item["acceptedAccuracy"] for item in successful]
            ),
            "meanCoverage": _mean([item["coverage"] for item in successful]),
            "meanSelectiveScore": _mean(
                [item["selectiveScore"] for item in successful]
            ),
            "meanGoldProbability": _mean(
                [
                    item["meanGoldProbability"]
                    for item in successful
                    if item["meanGoldProbability"] is not None
                ]
            ),
            "meanBinaryBrierScore": _mean(
                [
                    item["binaryBrierScore"]
                    for item in successful
                    if item["binaryBrierScore"] is not None
                ]
            ),
            "latencySeconds": {
                "mean": _mean(latencies),
                "p50": _percentile(latencies, 0.5),
                "p95": _percentile(latencies, 0.95),
            },
            "consistency": _consistency(successful, gold),
            "classifierInputTokens": input_tokens,
            "estimatedClassifierCostUsd": round(estimated_classifier_cost, 9),
            "fallbackTotalTokens": fallback_tokens,
            "reportedFallbackCostUsd": round(fallback_cost, 9),
            "estimatedCombinedClassificationCostUsd": round(
                estimated_classifier_cost + fallback_cost, 9
            ),
        }
    return result


def _consistency(
    trials: list[dict[str, Any]], gold: list[dict[str, Any]]
) -> dict[str, Any]:
    by_key: dict[str, list[str]] = {str(item["key"]): [] for item in gold}
    for trial in trials:
        for decision in trial["decisions"]:
            outcome = (
                json.dumps(decision["actual"], sort_keys=True)
                if decision["status"] == "accepted"
                else "<abstain>"
            )
            by_key[str(decision["key"])].append(outcome)
    detail: list[dict[str, Any]] = []
    changed_observations = 0
    total_observations = 0
    stable_keys = 0
    probability_ranges: list[float] = []
    for key, outcomes in by_key.items():
        counts = Counter(outcomes)
        modal_count = counts.most_common(1)[0][1] if counts else 0
        changed_observations += len(outcomes) - modal_count
        total_observations += len(outcomes)
        stable = len(counts) <= 1
        stable_keys += int(stable)
        probabilities = [
            float(decision["goldProbability"])
            for trial in trials
            for decision in trial["decisions"]
            if decision["key"] == key and decision["goldProbability"] is not None
        ]
        probability_range = (
            max(probabilities) - min(probabilities) if probabilities else None
        )
        if probability_range is not None:
            probability_ranges.append(probability_range)
        detail.append(
            {
                "key": key,
                "stable": stable,
                "outcomes": dict(sorted(counts.items())),
                "goldProbability": {
                    "mean": _mean(probabilities),
                    "minimum": round(min(probabilities), 6) if probabilities else None,
                    "maximum": round(max(probabilities), 6) if probabilities else None,
                    "range": round(probability_range, 6)
                    if probability_range is not None
                    else None,
                    "populationStdDev": round(statistics.pstdev(probabilities), 6)
                    if probabilities
                    else None,
                },
            }
        )
    return {
        "stableDecisionRate": _ratio(stable_keys, len(by_key)),
        "modalFlipRate": _ratio(changed_observations, total_observations),
        "meanGoldProbabilityRange": _mean(probability_ranges),
        "maximumGoldProbabilityRange": round(max(probability_ranges), 6)
        if probability_ranges
        else None,
        "decisions": detail,
    }


def _score_sysml(
    path: Path, content: str, case: dict[str, Any], repo: Path
) -> dict[str, Any]:
    concepts: list[dict[str, Any]] = []
    for concept in case.get("sysmlConcepts", []):
        matched_patterns = [
            pattern
            for pattern in concept["patterns"]
            if re.search(pattern, content, flags=re.IGNORECASE)
        ]
        evidence_paths = [repo / value for value in concept.get("evidencePaths", [])]
        concepts.append(
            {
                "id": concept["id"],
                "covered": bool(matched_patterns),
                "matchedPatterns": matched_patterns,
                "evidencePathsValid": all(item.exists() for item in evidence_paths),
            }
        )
    source_refs = re.findall(r"source\s*:\s*String\s*=\s*\"[^:\"]+:([^\"]+)\"", content)
    source_paths = [re.sub(r":\d+$", "", ref) for ref in source_refs]
    valid_source_refs = sum(
        (repo / ref.replace("\\", "/")).exists() for ref in source_paths
    )
    return {
        "name": path.stem,
        "path": str(path.resolve()),
        "characters": len(content),
        "partDefinitions": len(re.findall(r"\bpart\s+def\b", content)),
        "portDefinitions": len(re.findall(r"\bport\s+def\b", content)),
        "connections": len(re.findall(r"\bconnect\b", content)),
        "documentationBlocks": len(re.findall(r"\bdoc\b", content)),
        "sourceReferenceCount": len(source_refs),
        "validSourceReferenceRate": _ratio(valid_source_refs, len(source_refs)),
        "goldConceptCoverage": _ratio(
            sum(item["covered"] for item in concepts), len(concepts)
        ),
        "concepts": concepts,
    }


def _input_tokens(usage: dict[str, Any]) -> int:
    for key in ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _total_tokens(usage: dict[str, Any]) -> int:
    for key in ("total_tokens", "totalTokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _usage_cost(usage: dict[str, Any]) -> float:
    for key in ("calculatedCost", "cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _attach_resource_usage(trial: dict[str, Any], inventory: dict[str, Any]) -> None:
    trial["classifierUsage"] = inventory.get("classifier", {}).get("usage", {})
    trial["fallbackUsage"] = inventory.get("llmFallback", {}).get("usage", {})


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 6) if values else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return data


def _repository_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    if result.returncode or not commit:
        raise SystemExit(f"Could not determine Git commit for {repo}")
    return commit


def _read_url_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=30) as response:  # noqa: S310
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Expected a JSON object from {url}")
    return data


def _labeled_url(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise SystemExit("URL sources must use LABEL=URL")
    label, url = value.split("=", 1)
    if not label.strip() or not url.strip():
        raise SystemExit("URL sources must use a non-empty LABEL=URL")
    return label.strip(), url.strip()


def _print_summary(report: dict[str, Any], output: Path) -> None:
    candidate = report["evidence"]["candidateScore"]["overall"]
    print(
        f"Candidates: precision={candidate['precision']} recall={candidate['recall']} "
        f"({candidate['matchedCount']}/{candidate['expectedCount']} expected)"
    )
    for system, score in report["decisionEvaluation"]["bySystem"].items():
        print(
            f"{system}: trials={score['successfulTrialCount']}/{score['trialCount']} "
            f"accepted_accuracy={score['meanAcceptedAccuracy']} "
            f"coverage={score['meanCoverage']} "
            f"stable={score['consistency']['stableDecisionRate']} "
            f"flip_rate={score['consistency']['modalFlipRate']} "
            f"max_probability_range="
            f"{score['consistency']['maximumGoldProbabilityRange']} "
            f"classification_cost_usd="
            f"{score['estimatedCombinedClassificationCostUsd']}"
        )
    for score in report["sysmlEvaluation"]:
        print(
            f"{score['name']}: concept_coverage={score['goldConceptCoverage']} "
            f"valid_sources={score['validSourceReferenceRate']}"
        )
    print(f"Report: {output.resolve()}")


if __name__ == "__main__":
    raise SystemExit(main())
