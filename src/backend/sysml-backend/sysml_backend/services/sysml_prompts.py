"""SysML v2 analysis/enrichment prompt construction.

Pure prompt-building helpers, decoupled from any transport. The OpenCode client
(or any other caller) imports these to construct the messages it sends; nothing
here knows how the prompt is delivered. Keep this module free of I/O so prompts
can be built, inspected, and tested in isolation.

The model is built in two passes within one session:
  * Pass 1 (`analysis_prompt`)   — identify and define the ARCHITECTURE: every
    component, its ports, attributes, and how they connect. No documentation.
  * Pass 2 (`enrichment_prompt`) — add DEPTH and define PURPOSE: drill into each
    repository, fill in detail, and document what each element is responsible for.
"""

from __future__ import annotations

from typing import Any


def repo_lines(package_context: dict[str, Any]) -> list[str]:
    repositories = (
        package_context.get("repositories", [])
        if isinstance(package_context, dict)
        else []
    )
    # Paths arrive already expressed for the OpenCode container's mount (see
    # MonitoringService._opencode_package_context); emit them verbatim so the
    # agent can open them. Do NOT .resolve() — that would rewrite them against
    # the backend's filesystem, which the agent cannot see.
    return [
        "  - path: {path}{branch}{commit}".format(
            path=str(repo.get("path") or repo.get("remote_url") or "unknown"),
            branch=f"  branch: {repo['branch']}" if repo.get("branch") else "",
            commit=f"  commit: {repo['commit'][:8]}" if repo.get("commit") else "",
        )
        for repo in repositories
        if isinstance(repo, dict)
    ]


def analysis_system_prompt() -> str:
    """Pass 1 — architecture. Define the structural graph; purpose comes in pass 2."""
    return "\n".join(
        [
            "You are a SysML v2 architecture modeling agent. This is the FIRST of two passes.",
            "Your job here is to identify and define the ARCHITECTURE — the components, their",
            "interfaces (ports), their attributes, and how they connect. A second pass will add",
            "depth and write each element's purpose, so do NOT document what components do in this",
            "pass; concentrate on getting the structural graph complete and correctly wired.",
            "",
            "For each repository, identify:",
            "- Deployment style, such as Kubernetes, standalone, Argo, Helm, serverless, or other orchestrated runtime",
            "- External dependencies and services outside the repo, such as databases, OpenMetadata, queues,",
            "  object stores, identity providers, or third-party APIs",
            "- How it communicates with other applications, including protocol, direction, and whether the",
            "  dependency is shared",
            "- Network identity, such as hostnames, service names, ingress names, DNS names, or internal endpoints",
            "- The hosted image/service if it is clearly identifiable",
            "- Stable in-process runtime subsystems reached from production entry points, such as HTTP/API",
            "  servers, schedulers, execution engines, workers/runners, registries, and persistence layers",
            "",
            "Attributes — the ones below are the common, recommended vocabulary. Include any of them",
            "where the repo gives evidence, and ALSO add other attributes the repo clearly supports",
            "(e.g. `image`, `namespace`, `replicas`, `version`, `port`). Only `kind` is a fixed enum;",
            "every other attribute is open-ended — model what is relevant to the repo or service:",
            '  attribute env: String = "<environment>";   // e.g. "qa", "prod"',
            '  attribute kind: String = "<kind>";         // exactly one of: "internal", "external", "third-party", "owned"',
            '  attribute domain: String = "<domain>";     // e.g. "application", "data", "messaging", "identity", "cloud", "ingress", "external"',
            "  attribute stable: Boolean = true;          // omit if unknown",
            '  attribute deployment: String = "<style>";  // e.g. "argo", "raw-k8s", "helm", "compose"',
            '  attribute fqdn: String = "<hostname>";     // omit if unknown',
            '  attribute type: String = "<tech>";         // for sub-parts only: e.g. "postgresql", "redis", "rabbitmq", "pvc", "milvus", "minio"',
            "",
            "Port types — prefer the ones below when they fit. When the evidence shows a protocol not",
            "listed, add another `<Name>Port` type (e.g. `MetricsPort`, `LdapPort`, `SmtpPort`):",
            "  HttpPort      — REST or HTTP/S calls",
            "  AmqpPort      — AMQP message broker connections (RabbitMQ, etc.)",
            "  SsePort       — Server-Sent Events streams",
            "  WebSocketPort — WebSocket connections",
            "  GrpcPort      — gRPC calls",
            "  SqlPort       — direct database connections",
            "",
            "kind values:",
            '  "internal"    — a service defined in one of the listed repositories',
            '  "external"    — a shared cluster service not defined in the repos (database, broker, cache)',
            '  "third-party" — an external API or cloud service outside the cluster',
            '  "owned"       — a sub-part directly owned by its parent (database, cache, volume)',
            "",
            "Scope and evidence rules — establish these before drawing the graph:",
            "- Choose one concrete operating scope for the model: the canonical/default deployment when one",
            "  exists, otherwise the primary application runtime exposed by production entry points.",
            "- Record that choice inside `part MonitoredSuite` as",
            '  `attribute scope: String = "<specific deployment or runtime scope>";`.',
            "- Do not combine mutually exclusive modes. For example, do not put standalone SQLite and a",
            "  queue-mode PostgreSQL/Redis worker deployment into the same suite.",
            "- Treat README integration lists, changelogs, CI workflows, tests, fixtures, examples, benchmarks,",
            "  and devcontainers as search leads only. They are not proof that a component is in the chosen scope.",
            "- An environment variable or secret value is configuration, not evidence of an external secret-store",
            "  service. Model a secret store only when runtime code or the selected deployment calls one.",
            "- A compatibility endpoint (for example OpenAI-compatible HTTP) is an interface exposed by the",
            "  application, not an outbound dependency on the company or API named by that protocol.",
            "- Omit optional integrations unless the chosen deployment enables them. If an optional integration is",
            "  essential to explaining the architecture, mark it `attribute optional: Boolean = true;`.",
            '- Every top-level `part def` must include `attribute source: String = "repo:path";` pointing to',
            "  the strongest repository-relative runtime, deployment, or configuration file that supports it.",
            "  Do not invent line numbers or source paths.",
            "",
            "Part definition structure — use this ordering for every `part def`. Omit the `doc`:",
            "each element's purpose is written in the second pass, not here.",
            "",
            "  part def <ComponentName>Def :> BaseType {",
            '    attribute env: String = "<env>";',
            '    attribute kind: String = "<kind>";',
            '    attribute domain: String = "<domain>";',
            "    attribute stable: Boolean = true;",
            '    attribute deployment: String = "<style>";',
            '    attribute fqdn: String = "<hostname>";',
            '    attribute source: String = "<repo>:<path>";',
            "",
            "    port http: HttpPort { direction = in; }",
            "    port amqp: AmqpPort { direction = out; }",
            "",
            "    part database { ... }",
            "    part cache { ... }",
            "",
            "    connection db connect database.sql to postgres.sql;",
            "",
            "    action start;",
            "    state running;",
            "",
            "    constraint c1;",
            "  }",
            "",
            "Omit any section that has no evidence. `:> BaseType` is optional; omit it if no base type applies.",
            "",
            "Instances and connections — put them in the suite block, naming each instance with the",
            "plain component name (never a `suite_`, `inst_`, or any other prefix). Worked example:",
            "",
            "  part MonitoredSuite {",
            "    part broker : BrokerDef;",
            "    part orders : OrderServiceDef;",
            "    part postgres : PostgresDef;",
            "",
            "    connect orders.amqp to broker.amqp;",
            "    connect orders.sql to postgres.sql;",
            "  }",
            "",
            "Naming — keep identifiers human-readable and collision-free:",
            "- Name each `part def` with the component's PascalCase name plus a `Def` suffix (e.g. `BrokerDef`).",
            "- Name each instance with the plain component name (e.g. `part broker : BrokerDef;`).",
            "- Do NOT prefix instance names (no `suite_`, `inst_`, etc.) and never reuse the `part def`",
            "  identifier as the instance identifier — the suffix on the def is what keeps them distinct.",
            "- Use that same instance name in every `connect` statement that references it.",
            "",
            "Modeling requirements:",
            "- Model runtime components implemented by the repositories, plus external dependencies actually used",
            "  by the chosen operating scope, as top-level `part def` entries",
            "- A container or repository boundary is not always the architecture boundary. For a substantial",
            "  single-process application or monorepo, model distinct in-process subsystems that own runtime",
            "  responsibilities and communicate along a source-backed execution path.",
            "- Do not stop at one application box plus its database when production entry points clearly route",
            "  work through separate API, scheduler/execution, worker/runner, registry, or storage components.",
            "- Declare a `port` for every protocol the part exposes or consumes, using the port types above",
            "  or another `<Name>Port` where the evidence requires one",
            "- Every port must declare `direction = in`, `direction = out`, or `direction = both` in its block",
            "- Use `in` for provider/listener ports, `out` for caller/client ports, and `both` only for true",
            "  bidirectional channels such as WebSocket sessions or explicit publish/subscribe interfaces",
            "- Preserve communication direction in connections: connect the caller/client `out` port to the",
            "  provider/listener `in` port whenever the evidence shows request/response traffic",
            "- Where a part clearly owns a runtime resource (a dedicated database, cache, or PVC), capture it",
            '  as a nested `part` with `attribute kind: String = "owned"`; the second pass will deepen these',
            "- If multiple repositories share the same external service, model it once as a shared `part def`",
            "- Put every instance and every `connect` inside one `part MonitoredSuite { … }` block,",
            "  declaring each instance as `part <name> : <ComponentName>Def;`",
            "- Connections use the form `connect instanceName.portName to instanceName.portName;`",
            "- Model the connections you find evidence for; do not invent connections to look complete",
            "- Do NOT write `doc` comments in this pass — leave every element's purpose for the second pass",
            "- Do not put a `doc` on the package itself",
            "- Do not modify any repository files",
            "",
            "Where to look (architecture lives in deployment + config):",
            "- Start with root/default deployment manifests (Argo/Helm/k8s/compose), service and ingress definitions,",
            "  Dockerfiles, and dependency/config files (package manifests, .env templates) — these reveal",
            "  components, ports, network identity, and connections.",
            "- Read production entry points and runtime wiring when manifests do not establish the component graph.",
            "  Names in docs, tests, or dependency lists must be corroborated by the chosen runtime before modeling.",
            "- The pre-scan checklist is a navigation aid. Only its high-confidence coverage requirements are",
            "  mandatory, and only after the cited file is checked and confirmed to be in scope.",
            "- If evidence for an attribute is missing, omit it and move on.",
            "",
            "Output requirements:",
            "- Respond with SysML v2 syntax only",
            "- Do not include explanations, commentary, markdown, or prose outside the SysML v2 document",
            "- If evidence is insufficient for a detail, omit that attribute rather than guessing",
        ]
    )


def analysis_user_prompt(package_context: dict[str, Any]) -> str:
    package_name = (
        package_context.get("name") if isinstance(package_context, dict) else None
    )
    return "\n".join(
        [
            "Survey the repositories listed below and produce a single SysML v2 document that models",
            "the architecture of the package they form — the components inside it and how they communicate.",
            "",
            f"Project: {package_name or 'unknown'}",
            "",
            "Repos:",
            *repo_lines(package_context),
            "",
            "Pre-scan evidence checklist:",
            str(
                package_context.get("evidenceSummary")
                or "No deterministic repository evidence was extracted before synthesis."
            ),
            "",
            "Coverage requirements:",
            *[
                f"- {requirement}"
                for requirement in _coverage_requirements(package_context)
            ],
        ]
    )


def analysis_prompt(package_context: dict[str, Any]) -> str:
    """Pass 1 — the full architecture instruction as a single message (instructions + task + repos)."""
    return "\n\n".join(
        [analysis_system_prompt(), analysis_user_prompt(package_context)]
    )


def enrichment_prompt(package_context: dict[str, Any], system_map: str) -> str:
    """Pass 2 — add depth and define purpose over the architecture from pass 1."""
    return "\n".join(
        [
            "This is the SECOND of two passes. The architecture below is already defined — its",
            "components, ports, attributes, and connections. Your job now is to add DEPTH and define",
            "PURPOSE: drill into each repository, fill in the detail the first pass left out, and",
            "document what every element is responsible for.",
            "",
            "Architecture from the first pass:",
            system_map,
            "",
            "Repositories (absolute paths — read the files at these locations):",
            *repo_lines(package_context),
            "",
            "Pre-scan evidence checklist:",
            str(
                package_context.get("evidenceSummary")
                or "No deterministic repository evidence was extracted before synthesis."
            ),
            "",
            "Coverage requirements:",
            *[
                f"- {requirement}"
                for requirement in _coverage_requirements(package_context)
            ],
            "",
            "For each component, using evidence from its repository:",
            "- Define purpose: add a `doc` as the FIRST line inside its `{ }` block, stating in one",
            "  specific sentence the single responsibility it owns — be concrete, not generic. Do the",
            "  same for every owned sub-part and every `port`.",
            "- Add depth: owned sub-parts (database, cache, volume) it clearly owns, finer attributes,",
            "  and any port the deeper read reveals — each new sub-part and port also gets its `doc`.",
            "- Trace at least one primary production request or job from its entry point through the runtime.",
            "  Promote stable subsystems with distinct responsibilities into connected parts instead of leaving",
            "  a large application as one undifferentiated box.",
            "- Add or refine `connect` statements so every real interaction between components — and to",
            "  external services — is represented.",
            "- Add or refine port `direction` values. Use `in` for listeners/providers, `out` for callers/clients,",
            "  and reserve `both` for truly bidirectional traffic.",
            "- Preserve explicit architecture domains from the evidence checklist using `attribute domain`",
            "  where possible: application, ingress, data, messaging, identity, cloud, storage, operations, external.",
            "- Remove any first-pass component or connection that is supported only by documentation, tests,",
            "  examples, benchmarks, CI, or a mutually exclusive deployment mode. Accuracy outranks preservation.",
            "- Preserve the selected scope and every valid `source` attribute. Add or correct source paths when the",
            "  deeper read finds stronger runtime evidence.",
            "",
            "Documentation form:",
            "- Write every `doc` exactly as `doc /* … */`, placed as the FIRST line inside its block.",
            "  Use this form only — never `//`, `/** */`, `#`, or markdown, and never put an attribute,",
            "  port, or part before the `doc`.",
            "- Do not put a `doc` on the package itself.",
            "",
            "Keep the same document structure: top-level `part def` entries, one `part MonitoredSuite`",
            "block holding all instances and `connect` statements, and the `view`. Do not modify any",
            "repository files.",
            "",
            "Output the complete, documented SysML v2 document only, no explanation.",
        ]
    )


def repair_prompt(
    package_context: dict[str, Any],
    sysml_content: str,
    diagnostics: list[str],
    *,
    attempt: int,
) -> str:
    """Ask the agent to repair a generated model without redoing discovery."""
    return "\n".join(
        [
            f"This is SysML repair attempt {attempt}. The document below failed validation.",
            "Fix the document only. Do not modify repository files.",
            "",
            "Validation diagnostics:",
            *[f"- {diagnostic}" for diagnostic in diagnostics],
            "",
            "Repair requirements:",
            "- Return one complete SysML v2 document only.",
            "- Preserve evidence-backed architecture, but remove unsupported or out-of-scope elements.",
            "- Keep one `part MonitoredSuite { ... }` block with all top-level instances and connections.",
            "- Every `connect` endpoint must start with an instance declared in `part MonitoredSuite`.",
            "- Every declared instance must reference an existing top-level `part def`.",
            "- Every endpoint port, such as `service.sql`, must be declared on that instance's part definition.",
            "- If a connected database, cache, queue, or external service is missing, add both its `part def` and its suite instance.",
            "- If validation reports missing coverage for an evidence category, inspect the cited high-confidence",
            "  runtime file before adding corresponding components, ports, attributes, or connections.",
            "- Keep one explicit `attribute scope` in MonitoredSuite and one valid repository-relative",
            "  `attribute source` on every top-level part definition.",
            "- Replace generic `direction = both` ports with `in` or `out` where the connection has a clear caller and provider.",
            "- Never connect directly to a `part def` name unless that exact name is also declared as an instance.",
            "- Before responding, self-check every connection endpoint against the instance and port declarations.",
            "",
            "Repositories:",
            *repo_lines(package_context),
            "",
            "Pre-scan evidence checklist:",
            str(
                package_context.get("evidenceSummary")
                or "No deterministic repository evidence was extracted before synthesis."
            ),
            "",
            "Invalid SysML document:",
            sysml_content,
            "",
            "Output the corrected SysML v2 document only, no explanation.",
        ]
    )


def coverage_repair_prompt(
    package_context: dict[str, Any],
    sysml_content: str,
    diagnostics: list[str],
    *,
    attempt: int,
) -> str:
    """Ask the agent to improve a renderable model that missed evidence-backed coverage."""
    return "\n".join(
        [
            f"This is SysML coverage enhancement attempt {attempt}.",
            "The document below is syntactically usable, but validation found missing or weak architecture coverage.",
            "Improve coverage only. Do not modify repository files.",
            "",
            "Coverage diagnostics:",
            *[f"- {diagnostic}" for diagnostic in diagnostics],
            "",
            "Pre-scan evidence checklist:",
            str(
                package_context.get("evidenceSummary")
                or "No deterministic repository evidence was extracted before synthesis."
            ),
            "",
            "Coverage requirements:",
            *[
                f"- {requirement}"
                for requirement in _coverage_requirements(package_context)
            ],
            "",
            "Enhancement requirements:",
            "- Return one complete SysML v2 document only.",
            "- Preserve evidence-backed elements; remove anything supported only by docs, tests, CI, examples,",
            "  benchmarks, or a mutually exclusive operating mode.",
            "- Add missing architecture concepts only after checking the cited high-confidence runtime evidence.",
            "- Keep one explicit `attribute scope` in MonitoredSuite and add a valid repository-relative",
            "  `attribute source` to every top-level part definition.",
            "- Pay special attention to ingress/users, network/cloud platform, identity/secrets, observability, backup, storage, databases, messaging, and third-party services.",
            "- Add `attribute domain` to important components using stable values such as application, ingress, data, messaging, identity, cloud, storage, operations, observability, external, ai, search, or metadata.",
            "- Keep each port direction specific: `in` for providers/listeners, `out` for callers/clients, and `both` only for true bidirectional traffic.",
            "- Keep one `part MonitoredSuite { ... }` block with all top-level instances and connections.",
            "- Every new connection endpoint must reference an instance and port declared in the returned document.",
            "- Include a named architecture `view` exposing `MonitoredSuite::*` if the document does not already contain a view.",
            "",
            "Repositories:",
            *repo_lines(package_context),
            "",
            "Current SysML document:",
            sysml_content,
            "",
            "Output the improved SysML v2 document only, no explanation.",
        ]
    )


def _coverage_requirements(package_context: dict[str, Any]) -> list[str]:
    requirements = package_context.get("coverageRequirements")
    if not isinstance(requirements, list):
        return []
    return [str(requirement) for requirement in requirements if requirement]
