from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib.util import find_spec
from typing import Any

logger = logging.getLogger(__name__)

APPLICATION_TYPES: dict[str, str] = {
    "api_service": "An HTTP, RPC, GraphQL, or other backend service API.",
    "frontend": "A browser, desktop, mobile, or other user-facing application.",
    "worker": "A background worker, scheduled job, or event consumer.",
    "library": "A reusable package, SDK, framework, or shared library.",
    "data_pipeline": "A batch, streaming, analytics, ETL, or data-processing application.",
    "infrastructure": "Infrastructure, deployment, platform, or configuration definitions.",
    "unknown": "The supplied evidence is insufficient for a reliable classification.",
}

DEPENDENCY_TYPES: dict[str, str] = {
    "runtime_database": "A database used by the application at runtime.",
    "internal_service": "Another application, repository, or service owned by the system.",
    "messaging": "A message broker, queue, event bus, or streaming dependency.",
    "cache": "A cache, in-memory data grid, or ephemeral key-value dependency.",
    "storage": "Object, file, block, network, or persistent storage.",
    "external_api": "A separately operated service or third-party API.",
    "platform_service": "Cloud, identity, ingress, search, vector, observability, or platform infrastructure.",
    "unknown": "The supplied evidence is insufficient for a reliable classification.",
}

_DEPENDENCY_CATEGORIES = {
    "cache",
    "cloud",
    "database",
    "external_api",
    "identity",
    "ingress",
    "messaging",
    "observability",
    "search",
    "service",
    "storage",
    "vector",
}

_GENERIC_SIGNAL_NAMES = {
    "aws",
    "database_url",
    "jdbc_database_url",
    "pgport",
    "pghost",
    "postgres",
    "postgresql",
}


@dataclass(frozen=True)
class ArchitectureClassifierConfig:
    api_key: str | None
    base_url: str
    model: str
    confidence_threshold: float
    timeout_seconds: float


class ArchitectureClassifier:
    """Classify deterministic repository evidence with Jev.

    Candidate extraction and stable identity are deterministic. Jev owns typed
    classification and semantic duplicate judgments. Low-confidence or explicit
    ``unknown`` answers remain unresolved for the OpenCode fallback.
    """

    def __init__(
        self,
        config: ArchitectureClassifierConfig,
        *,
        client: Any | None = None,
    ) -> None:
        if not 0.5 < config.confidence_threshold <= 1.0:
            raise ValueError(
                "JEV_CONFIDENCE_THRESHOLD must be greater than 0.5 and at most 1.0."
            )
        self.config = config
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    @property
    def available(self) -> bool:
        return find_spec("typesafe_sdk") is not None

    def classify(
        self, package: dict[str, Any], evidence: dict[str, Any]
    ) -> dict[str, Any]:
        components = _component_candidates(package, evidence)
        dependencies = _dependency_candidates(components, evidence)
        duplicate_pairs = _duplicate_pairs(dependencies)
        inventory: dict[str, Any] = {
            "schemaVersion": 1,
            "components": components,
            "dependencies": dependencies,
            "deduplication": {"decisions": [], "mergedGroups": []},
            "unresolved": [],
            "classifier": {
                "provider": "typesafe",
                "model": self.config.model,
                "configured": self.configured,
                "available": self.available,
                "confidenceThreshold": self.config.confidence_threshold,
                "status": "pending",
                "usage": {},
            },
        }

        if not components and not dependencies:
            inventory["classifier"]["status"] = "no_candidates"
            return summarize_architecture(inventory)

        if not self.configured:
            inventory["classifier"]["status"] = "unconfigured"
            _mark_all_unresolved(
                inventory,
                duplicate_pairs,
                source="jev_unconfigured",
                reason="TYPESAFE_API_KEY is not configured.",
            )
            return summarize_architecture(inventory)

        try:
            from typesafe_sdk import Choice, Noul, TypeSafeClient

            questions = _questions(
                components,
                dependencies,
                duplicate_pairs,
                choice_type=Choice,
                noul_type=Noul,
            )
            client = self._client or TypeSafeClient(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                model=self.config.model,
                timeout=self.config.timeout_seconds,
            )
            response = client.system_one(
                state={
                    "system": str(package.get("name") or "system"),
                    "components": components,
                    "dependencies": dependencies,
                    "duplicateCandidates": duplicate_pairs,
                },
                questions=questions,
                model=self.config.model,
            )
        except ImportError:
            inventory["classifier"]["status"] = "unavailable"
            _mark_all_unresolved(
                inventory,
                duplicate_pairs,
                source="jev_unavailable",
                reason="The optional typesafe-sdk package is not installed.",
            )
            return summarize_architecture(inventory)
        except Exception as error:
            logger.warning("[jev] architecture classification failed: %s", error)
            inventory["classifier"]["status"] = "error"
            inventory["classifier"]["error"] = type(error).__name__
            _mark_all_unresolved(
                inventory,
                duplicate_pairs,
                source="jev_error",
                reason=f"Jev request failed with {type(error).__name__}.",
            )
            return summarize_architecture(inventory)

        inventory["classifier"]["status"] = "completed"
        inventory["classifier"]["model"] = str(
            getattr(response, "model", self.config.model)
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            inventory["classifier"]["usage"] = _model_dump(usage)
        answers = getattr(response, "answers", {})
        _apply_jev_answers(
            inventory,
            answers if isinstance(answers, dict) else {},
            duplicate_pairs,
            self.config.confidence_threshold,
        )
        return summarize_architecture(inventory)


def apply_llm_architecture_decisions(
    inventory: dict[str, Any], decisions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply validated OpenCode decisions to unresolved Jev questions."""

    unresolved = {
        str(item.get("id")): item
        for item in inventory.get("unresolved", [])
        if isinstance(item, dict) and item.get("id")
    }
    applied: set[str] = set()
    components = {
        str(item.get("questionId")): item
        for item in inventory.get("components", [])
        if isinstance(item, dict)
    }
    dependencies = {
        str(item.get("questionId")): item
        for item in inventory.get("dependencies", [])
        if isinstance(item, dict)
    }
    dedupe = {
        str(item.get("id")): item
        for item in inventory.get("deduplication", {}).get("decisions", [])
        if isinstance(item, dict)
    }

    for raw in decisions:
        if not isinstance(raw, dict):
            continue
        decision_id = str(raw.get("id") or "")
        pending = unresolved.get(decision_id)
        if pending is None:
            continue
        confidence = _bounded_float(raw.get("confidence"))
        reason = str(raw.get("reason") or "LLM fallback decision.").strip()
        kind = str(pending.get("kind") or "")
        if kind in {"component_classification", "dependency_classification"}:
            allowed = pending.get("allowedValues", [])
            value = str(raw.get("value") or "")
            if value not in allowed or value == "unknown":
                continue
            target = components.get(decision_id) or dependencies.get(decision_id)
            if target is None:
                continue
            target["classification"] = value
            target["decision"] = {
                "source": "llm_fallback",
                "status": "accepted",
                "confidence": confidence,
                "reasoningSummary": reason,
                "priorDecision": target.get("decision", {}),
            }
            applied.add(decision_id)
        elif kind == "dependency_identity":
            same = raw.get("same")
            if not isinstance(same, bool):
                continue
            target = dedupe.get(decision_id)
            if target is None:
                continue
            target.update(
                {
                    "same": same,
                    "decision": {
                        "source": "llm_fallback",
                        "status": "accepted",
                        "confidence": confidence,
                        "reasoningSummary": reason,
                        "priorDecision": target.get("decision", {}),
                    },
                }
            )
            applied.add(decision_id)

    inventory["unresolved"] = [
        item
        for item in inventory.get("unresolved", [])
        if isinstance(item, dict) and str(item.get("id")) not in applied
    ]
    inventory["llmFallback"] = {
        "status": "completed" if applied else "no_valid_decisions",
        "decisionCount": len(applied),
    }
    return summarize_architecture(inventory)


def finalize_architecture(inventory: dict[str, Any]) -> dict[str, Any]:
    """Combine dependencies only when identity was explicitly accepted."""

    dependencies = [
        item
        for item in inventory.get("dependencies", [])
        if isinstance(item, dict) and item.get("key")
    ]
    parent = {str(item["key"]): str(item["key"]) for item in dependencies}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for decision in inventory.get("deduplication", {}).get("decisions", []):
        if not isinstance(decision, dict) or decision.get("same") is not True:
            continue
        if decision.get("decision", {}).get("status") != "accepted":
            continue
        left = str(decision.get("leftKey") or "")
        right = str(decision.get("rightKey") or "")
        if left in parent and right in parent:
            union(left, right)

    groups: dict[str, list[dict[str, Any]]] = {}
    for dependency in dependencies:
        groups.setdefault(find(str(dependency["key"])), []).append(dependency)

    merged: list[dict[str, Any]] = []
    merged_groups: list[dict[str, Any]] = []
    for members in groups.values():
        combined = _combine_dependencies(members)
        merged.append(combined)
        if len(members) > 1:
            merged_groups.append(
                {
                    "key": combined["key"],
                    "memberKeys": sorted(str(member["key"]) for member in members),
                    "evidenceRefs": combined["evidenceRefs"],
                }
            )
    inventory["dependencies"] = sorted(merged, key=lambda item: str(item["key"]))
    inventory.setdefault("deduplication", {})["mergedGroups"] = merged_groups
    return summarize_architecture(inventory)


def summarize_architecture(inventory: dict[str, Any]) -> dict[str, Any]:
    components = inventory.get("components", [])
    dependencies = inventory.get("dependencies", [])
    unresolved = inventory.get("unresolved", [])
    inventory["summary"] = {
        "componentCount": len(components) if isinstance(components, list) else 0,
        "dependencyCount": len(dependencies) if isinstance(dependencies, list) else 0,
        "unresolvedCount": len(unresolved) if isinstance(unresolved, list) else 0,
        "mergedDependencyGroupCount": len(
            inventory.get("deduplication", {}).get("mergedGroups", [])
        ),
    }
    return inventory


def llm_architecture_prompt(inventory: dict[str, Any]) -> str:
    return (
        "Resolve the following architecture classification decisions. Use only the "
        "allowed values supplied for each item. For dependency_identity items, return "
        "same=true only when both records are the same logical dependency; dependencies "
        "may share a topic and still be distinct. Give a concise evidence-based reason, "
        "not hidden chain-of-thought. Return JSON only in this exact shape: "
        '{"decisions":[{"id":"...","value":"...","same":true,'
        '"confidence":0.0,"reason":"..."}]}. Omit value for identity decisions and '
        "omit same for classification decisions.\n\n"
        + json.dumps(
            {
                "unresolved": inventory.get("unresolved", []),
                "components": inventory.get("components", []),
                "dependencies": inventory.get("dependencies", []),
            },
            separators=(",", ":"),
        )
    )


def _component_candidates(
    package: dict[str, Any], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    records = evidence.get("records", [])
    records = records if isinstance(records, list) else []
    repositories = package.get("repositories", [])
    repositories = repositories if isinstance(repositories, list) else []
    components: list[dict[str, Any]] = []
    for index, repository in enumerate(repositories):
        if not isinstance(repository, dict):
            continue
        name = _repository_name(repository, index)
        repo_records = [
            record
            for record in records
            if isinstance(record, dict) and str(record.get("repository")) == name
        ]
        components.append(
            {
                "key": _stable_key("component", name),
                "name": name,
                "repository": {
                    "path": str(repository.get("path") or ""),
                    "role": str(repository.get("role") or "unknown"),
                    "url": str(
                        repository.get("url")
                        or repository.get("remote_url")
                        or repository.get("remoteUrl")
                        or ""
                    ),
                },
                "evidenceRefs": sorted(
                    {_evidence_ref(record) for record in repo_records}
                ),
                "evidence": [_compact_record(record) for record in repo_records],
                "classification": "unknown",
                "questionId": f"component_{len(components)}",
            }
        )
    return components


def _dependency_candidates(
    components: list[dict[str, Any]], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    component_by_name = {str(item["name"]): item for item in components}
    grouped: dict[str, dict[str, Any]] = {}
    records = evidence.get("records", [])
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        topic = str(record.get("category") or "")
        if topic not in _DEPENDENCY_CATEGORIES:
            continue
        source = component_by_name.get(str(record.get("repository") or ""))
        if source is None:
            continue
        target_name = _dependency_target(record)
        target_component = _match_component(target_name, components, source)
        key = _stable_key("dependency", str(source["key"]), topic, target_name)
        candidate = grouped.setdefault(
            key,
            {
                "key": key,
                "sourceComponentKey": source["key"],
                "topic": topic,
                "targetName": target_name,
                "targetComponentKey": target_component.get("key")
                if target_component
                else None,
                "aliases": [],
                "evidenceRefs": [],
                "evidence": [],
                "classification": "unknown",
            },
        )
        candidate["evidenceRefs"].append(_evidence_ref(record))
        candidate["evidence"].append(_compact_record(record))

    dependencies = sorted(grouped.values(), key=lambda item: str(item["key"]))
    for index, dependency in enumerate(dependencies):
        dependency["evidenceRefs"] = sorted(set(dependency["evidenceRefs"]))
        dependency["questionId"] = f"dependency_{index}"
    return dependencies


def _duplicate_pairs(dependencies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(dependencies):
        for right in dependencies[index + 1 :]:
            if left.get("sourceComponentKey") != right.get("sourceComponentKey"):
                continue
            if left.get("topic") != right.get("topic"):
                continue
            if not _plausibly_same_target(left, right):
                continue
            pairs.append(
                {
                    "id": f"dedupe_{len(pairs)}",
                    "leftKey": left["key"],
                    "rightKey": right["key"],
                    "topic": left["topic"],
                    "leftTarget": left["targetName"],
                    "rightTarget": right["targetName"],
                }
            )
    return pairs


def _questions(
    components: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    duplicate_pairs: list[dict[str, Any]],
    *,
    choice_type: Any,
    noul_type: Any,
) -> dict[str, Any]:
    questions: dict[str, Any] = {}
    for component in components:
        questions[str(component["questionId"])] = choice_type(
            instructions=(
                f"Classify component {component['key']} by its primary application role. "
                "Choose unknown when evidence is insufficient."
            ),
            criteria=APPLICATION_TYPES,
        )
    for dependency in dependencies:
        questions[str(dependency["questionId"])] = choice_type(
            instructions=(
                f"Classify dependency {dependency['key']} by how the source application "
                "uses the target. Choose unknown when evidence is insufficient."
            ),
            criteria=DEPENDENCY_TYPES,
        )
    for pair in duplicate_pairs:
        questions[str(pair["id"])] = noul_type(
            instructions=(
                f"Are {pair['leftKey']} and {pair['rightKey']} the same logical dependency, "
                "rather than two distinct dependencies that happen to share a topic?"
            )
        )
    return questions


def _apply_jev_answers(
    inventory: dict[str, Any],
    answers: dict[str, Any],
    duplicate_pairs: list[dict[str, Any]],
    threshold: float,
) -> None:
    for kind, items, allowed in (
        ("component_classification", inventory["components"], APPLICATION_TYPES),
        ("dependency_classification", inventory["dependencies"], DEPENDENCY_TYPES),
    ):
        for item in items:
            question_id = str(item["questionId"])
            answer = answers.get(question_id)
            selected = str(getattr(answer, "choice", "unknown") or "unknown")
            confidence = _bounded_float(getattr(answer, "confidence", 0.0))
            decision = {
                "source": "jev",
                "status": "accepted"
                if selected != "unknown" and confidence >= threshold
                else "unresolved",
                "confidence": confidence,
                "proposedClassification": selected,
                "probabilities": dict(getattr(answer, "probabilities", {}) or {}),
                "reasoningSummary": (
                    f"Jev selected {selected} with confidence {confidence:.3f}."
                ),
            }
            item["classification"] = (
                selected if decision["status"] == "accepted" else "unknown"
            )
            item["decision"] = decision
            if decision["status"] != "accepted":
                inventory["unresolved"].append(
                    {
                        "id": question_id,
                        "kind": kind,
                        "subjectKey": item["key"],
                        "allowedValues": list(allowed),
                        "jevDecision": decision,
                    }
                )

    for pair in duplicate_pairs:
        question_id = str(pair["id"])
        answer = answers.get(question_id)
        probability = _bounded_float(getattr(answer, "noul", 0.5))
        accepted = probability >= threshold or probability <= 1.0 - threshold
        decision = {
            **pair,
            "same": probability >= threshold if accepted else None,
            "decision": {
                "source": "jev",
                "status": "accepted" if accepted else "unresolved",
                "confidence": max(probability, 1.0 - probability),
                "probabilitySame": probability,
                "reasoningSummary": (
                    f"Jev assigned {probability:.3f} probability that the dependencies are identical."
                ),
            },
        }
        inventory["deduplication"]["decisions"].append(decision)
        if not accepted:
            inventory["unresolved"].append(
                {
                    "id": question_id,
                    "kind": "dependency_identity",
                    "leftKey": pair["leftKey"],
                    "rightKey": pair["rightKey"],
                    "allowedValues": [True, False],
                    "jevDecision": decision["decision"],
                }
            )


def _mark_all_unresolved(
    inventory: dict[str, Any],
    duplicate_pairs: list[dict[str, Any]],
    *,
    source: str,
    reason: str,
) -> None:
    for kind, items, allowed in (
        ("component_classification", inventory["components"], APPLICATION_TYPES),
        ("dependency_classification", inventory["dependencies"], DEPENDENCY_TYPES),
    ):
        for item in items:
            decision = {
                "source": source,
                "status": "unresolved",
                "confidence": None,
                "reasoningSummary": reason,
            }
            item["decision"] = decision
            inventory["unresolved"].append(
                {
                    "id": item["questionId"],
                    "kind": kind,
                    "subjectKey": item["key"],
                    "allowedValues": list(allowed),
                    "jevDecision": decision,
                }
            )
    for pair in duplicate_pairs:
        decision = {
            **pair,
            "same": None,
            "decision": {
                "source": source,
                "status": "unresolved",
                "confidence": None,
                "reasoningSummary": reason,
            },
        }
        inventory["deduplication"]["decisions"].append(decision)
        inventory["unresolved"].append(
            {
                "id": pair["id"],
                "kind": "dependency_identity",
                "leftKey": pair["leftKey"],
                "rightKey": pair["rightKey"],
                "allowedValues": [True, False],
                "jevDecision": decision["decision"],
            }
        )


def _combine_dependencies(members: list[dict[str, Any]]) -> dict[str, Any]:
    primary = max(
        members,
        key=lambda item: _bounded_float(item.get("decision", {}).get("confidence")),
    )
    member_keys = sorted(str(item["key"]) for item in members)
    aliases = sorted(
        {
            str(alias)
            for item in members
            for alias in [item.get("targetName"), *item.get("aliases", [])]
            if alias
        }
    )
    return {
        **primary,
        "key": member_keys[0],
        "aliases": aliases,
        "evidenceRefs": sorted(
            {
                str(ref)
                for item in members
                for ref in item.get("evidenceRefs", [])
                if ref
            }
        ),
        "evidence": [
            record
            for item in members
            for record in item.get("evidence", [])
            if isinstance(record, dict)
        ],
        "memberKeys": member_keys,
        "classificationDecisions": [
            item.get("decision", {}) for item in members if item.get("decision")
        ],
    }


def _plausibly_same_target(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_name = _normalize(str(left.get("targetName") or ""))
    right_name = _normalize(str(right.get("targetName") or ""))
    if not left_name or not right_name:
        return False
    if left_name in right_name or right_name in left_name:
        return True
    left_kinds = {str(record.get("kind")) for record in left.get("evidence", [])}
    right_kinds = {str(record.get("kind")) for record in right.get("evidence", [])}
    if left_kinds & right_kinds:
        return True
    return SequenceMatcher(None, left_name, right_name).ratio() >= 0.65


def _repository_name(repository: dict[str, Any], index: int) -> str:
    raw = repository.get("name") or repository.get("path") or f"repository-{index + 1}"
    return str(raw).rstrip("/\\").split("/")[-1].split("\\")[-1]


def _dependency_target(record: dict[str, Any]) -> str:
    name = str(record.get("name") or "").strip()
    kind = str(record.get("kind") or record.get("category") or "dependency").strip()
    return kind if _normalize(name) in _GENERIC_SIGNAL_NAMES else (name or kind)


def _match_component(
    target_name: str,
    components: list[dict[str, Any]],
    source: dict[str, Any],
) -> dict[str, Any] | None:
    target = re.sub(
        r"_(?:base_url|service_url|api_url|endpoint|host)$",
        "",
        _normalize(target_name),
    )
    if not target:
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for component in components:
        if component.get("key") == source.get("key"):
            continue
        component_name = _normalize(str(component.get("name") or ""))
        component_stem = re.sub(r"_(?:api|service|app|worker)$", "", component_name)
        if not component_stem:
            continue
        score = SequenceMatcher(None, target, component_stem).ratio()
        if target in component_name or component_stem in target:
            score = max(score, 0.9)
        candidates.append((score, component))
    if not candidates:
        return None
    score, component = max(candidates, key=lambda item: item[0])
    return component if score >= 0.75 else None


def _evidence_ref(record: dict[str, Any]) -> str:
    return (
        f"{record.get('repository', 'repository')}:"
        f"{str(record.get('path', '')).replace(chr(92), '/')}:"
        f"{record.get('line', 0)}"
    )


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in ("category", "kind", "name", "repository", "path", "line", "excerpt")
    }


def _stable_key(prefix: str, *parts: str) -> str:
    normalized = [_normalize(part) or "unknown" for part in parts]
    return ":".join([prefix, *normalized])


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _bounded_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, dict):
        return value
    return {}
