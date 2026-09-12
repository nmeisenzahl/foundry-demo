"""Pipeline evidence writers and aggregate release manifest generation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foundry_demo.delivery.automation import (
    AutomationConfig,
    AutomationConfigError,
    load_automation_config,
    select_agents,
)

JOB_EVIDENCE_SCHEMA_VERSION = "1"
MANIFEST_SCHEMA_VERSION = "1"
JOB_EVIDENCE_KIND = "job_evidence"

AGENT_KINDS = frozenset({"prompt", "hosted"})
JOB_STATUSES = frozenset({"succeeded", "failed"})
DEPLOYMENT_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})
STEP_OUTCOMES = frozenset({"succeeded", "failed", "skipped", "not_applicable"})
JOB_EVIDENCE_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "agent_name",
        "agent_kind",
        "environment",
        "run_id",
        "run_attempt",
        "job_status",
        "image_build_outcome",
        "deploy_outcome",
        "image_reference",
        "image_digest",
        "deployment_record",
    }
)

HOSTED_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class JobEvidence:
    agent_name: str
    agent_kind: str
    environment: str
    run_id: str
    run_attempt: int
    job_status: str
    image_build_outcome: str
    deploy_outcome: str
    image_reference: str | None
    image_digest: str | None
    deployment_record: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_name", self.agent_name.strip())
        object.__setattr__(self, "agent_kind", self.agent_kind.strip())
        object.__setattr__(self, "environment", self.environment.strip())
        object.__setattr__(self, "run_id", self.run_id.strip())
        object.__setattr__(self, "job_status", self.job_status.strip())
        object.__setattr__(self, "image_build_outcome", self.image_build_outcome.strip())
        object.__setattr__(self, "deploy_outcome", self.deploy_outcome.strip())

        if self.image_reference is not None:
            object.__setattr__(self, "image_reference", self.image_reference.strip())
        if self.image_digest is not None:
            object.__setattr__(self, "image_digest", self.image_digest.strip())
        if self.deployment_record is not None:
            object.__setattr__(self, "deployment_record", self.deployment_record.strip())

        _require_nonblank(self.agent_name, field_name="agent_name")
        _require_nonblank(self.environment, field_name="environment")
        _require_nonblank(self.run_id, field_name="run_id")
        if type(self.run_attempt) is not int or self.run_attempt <= 0:
            raise ValueError("run_attempt must be a positive integer.")
        _require_value(self.agent_kind, allowed=AGENT_KINDS, field_name="agent_kind")
        _require_value(self.job_status, allowed=JOB_STATUSES, field_name="job_status")
        _require_value(
            self.image_build_outcome,
            allowed=STEP_OUTCOMES,
            field_name="image_build_outcome",
        )
        _require_value(self.deploy_outcome, allowed=STEP_OUTCOMES, field_name="deploy_outcome")

        if self.image_reference == "":
            raise ValueError("image_reference cannot be blank when provided.")
        if self.image_digest == "":
            raise ValueError("image_digest cannot be blank when provided.")
        if self.deployment_record == "":
            raise ValueError("deployment_record cannot be blank when provided.")

        if self.agent_kind == "prompt" and (
            self.image_reference is not None or self.image_digest is not None
        ):
            raise ValueError(
                "prompt agent evidence must not define image_reference or image_digest."
            )

        if self.agent_kind == "hosted":
            has_reference = self.image_reference is not None
            has_digest = self.image_digest is not None
            if has_reference != has_digest:
                raise ValueError(
                    "hosted agent evidence must define image_reference and image_digest together."
                )
            if has_digest and not HOSTED_DIGEST_PATTERN.match(self.image_digest):
                raise ValueError(
                    "image_digest for hosted agents must match sha256:<64 lowercase hex>."
                )
            if (
                self.job_status == "succeeded"
                and self.deploy_outcome == "succeeded"
                and (
                    self.image_reference is None
                    or self.image_digest is None
                    or not _is_immutable_image_reference(
                        self.image_reference, self.image_digest
                    )
                )
            ):
                raise ValueError(
                    "Successful hosted job evidence must include an immutable "
                    "image_reference and matching image_digest."
                )


@dataclass(frozen=True)
class ReleaseMetadata:
    environment: str
    workflow_url: str | None
    repository: str | None
    ref: str | None
    commit: str | None
    actor: str | None
    event: str | None
    run_id: str | None
    run_attempt: str | None
    deployment_result: str
    monitoring_url: str | None
    generated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", self.environment.strip())
        object.__setattr__(self, "generated_at", self.generated_at.strip())
        object.__setattr__(self, "deployment_result", self.deployment_result.strip())
        _require_nonblank(self.environment, field_name="environment")
        _require_nonblank(self.generated_at, field_name="generated_at")
        _require_value(
            self.deployment_result,
            allowed=DEPLOYMENT_RESULTS,
            field_name="deployment_result",
        )

        for field_name in (
            "workflow_url",
            "repository",
            "ref",
            "commit",
            "actor",
            "event",
            "run_id",
            "run_attempt",
            "monitoring_url",
        ):
            value = getattr(self, field_name)
            if value is not None:
                cleaned = value.strip()
                if not cleaned:
                    raise ValueError(f"{field_name} cannot be blank when provided.")
                object.__setattr__(self, field_name, cleaned)


def _require_nonblank(value: str, *, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} cannot be blank.")


def _require_value(value: str, *, allowed: frozenset[str], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(
            f"{field_name} must be one of {sorted(allowed)}; received {value!r}."
        )


def _write_json_atomic(output: Path, payload: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output.parent / f".tmp_{output.name}_{uuid.uuid4().hex}.json"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, output)


def write_job_evidence(output: Path, evidence: JobEvidence) -> None:
    payload: dict[str, Any] = {
        "schema_version": JOB_EVIDENCE_SCHEMA_VERSION,
        "kind": JOB_EVIDENCE_KIND,
        **asdict(evidence),
    }
    _write_json_atomic(output, payload)


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read evidence file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Evidence file is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"Evidence file must be a JSON object: {path}")
    return payload


def _read_required_string(payload: Mapping[str, Any], *, key: str, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Invalid job evidence in {path}: {key} must be a string.")
    return value


def _read_optional_string(payload: Mapping[str, Any], *, key: str, path: Path) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"Invalid job evidence in {path}: {key} must be a string or null.")


def _read_required_integer(payload: Mapping[str, Any], *, key: str, path: Path) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise ValueError(f"Invalid job evidence in {path}: {key} must be an integer.")
    return value


def _parse_job_evidence_payload(payload: Mapping[str, Any], path: Path) -> JobEvidence:
    missing = sorted(JOB_EVIDENCE_REQUIRED_KEYS - set(payload.keys()))
    if missing:
        raise ValueError(f"Invalid job evidence in {path}: missing keys {missing}")
    if payload.get("schema_version") != JOB_EVIDENCE_SCHEMA_VERSION:
        schema = payload.get("schema_version")
        raise ValueError(
            f"Invalid job evidence in {path}: schema_version must be "
            f"{JOB_EVIDENCE_SCHEMA_VERSION!r}; received {schema!r}."
        )
    if payload.get("kind") != JOB_EVIDENCE_KIND:
        kind = payload.get("kind")
        raise ValueError(
            f"Invalid job evidence in {path}: kind must be {JOB_EVIDENCE_KIND!r}; "
            f"received {kind!r}."
        )

    return JobEvidence(
        agent_name=_read_required_string(payload, key="agent_name", path=path),
        agent_kind=_read_required_string(payload, key="agent_kind", path=path),
        environment=_read_required_string(payload, key="environment", path=path),
        run_id=_read_required_string(payload, key="run_id", path=path),
        run_attempt=_read_required_integer(payload, key="run_attempt", path=path),
        job_status=_read_required_string(payload, key="job_status", path=path),
        image_build_outcome=_read_required_string(
            payload, key="image_build_outcome", path=path
        ),
        deploy_outcome=_read_required_string(payload, key="deploy_outcome", path=path),
        image_reference=_read_optional_string(payload, key="image_reference", path=path),
        image_digest=_read_optional_string(payload, key="image_digest", path=path),
        deployment_record=_read_optional_string(payload, key="deployment_record", path=path),
    )


def _looks_like_deployment_record(payload: Mapping[str, Any]) -> bool:
    schema = payload.get("schema_version")
    return (
        schema in {"3", "4", "5"}
        and isinstance(payload.get("deployment_id"), str)
        and isinstance(payload.get("agent_name"), str)
    )


def _normalize_record_reference(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def _is_immutable_image_reference(reference: str, digest: str) -> bool:
    return reference.endswith(f"@{digest}")


def _collect_evidence(
    evidence_root: Path,
    *,
    run_id: str | None,
) -> dict[str, tuple[Path, JobEvidence]]:
    """Select the newest job evidence per agent across downloaded attempts."""
    job_by_agent: dict[str, tuple[Path, JobEvidence]] = {}
    job_by_attempt: dict[tuple[str, int], Path] = {}

    if not evidence_root.exists():
        return {}
    if not evidence_root.is_dir():
        raise ValueError(f"evidence_root must be a directory: {evidence_root}")

    for path in sorted(evidence_root.rglob("*.json")):
        payload = _load_json(path)
        if payload.get("kind") != JOB_EVIDENCE_KIND:
            continue

        evidence = _parse_job_evidence_payload(payload, path)
        if evidence.run_id != run_id:
            raise ValueError(
                f"Job evidence in {path} has run_id {evidence.run_id!r}, "
                f"expected {run_id!r}."
            )
        attempt_key = (evidence.agent_name, evidence.run_attempt)
        existing_path = job_by_attempt.get(attempt_key)
        if existing_path is not None:
            raise ValueError(
                "Duplicate job evidence for agent "
                f"{evidence.agent_name!r} at run_attempt {evidence.run_attempt}: "
                f"{existing_path} and {path}"
            )
        job_by_attempt[attempt_key] = path
        existing = job_by_agent.get(evidence.agent_name)
        if existing is None or evidence.run_attempt > existing[1].run_attempt:
            job_by_agent[evidence.agent_name] = (path, evidence)

    return job_by_agent


def _resolve_deployment_record(*, job: JobEvidence, job_path: Path) -> dict[str, Any] | None:
    """Load the record deploy-agent reported for this job, if it was uploaded."""
    if job.deployment_record is None:
        return None

    # Each downloaded artifact keeps sibling evidence/ and deployments/ directories,
    # and deploy-agent reports the exact record path it wrote for this run.
    artifact_root = job_path.parent.parent
    declared_path = _normalize_record_reference(job.deployment_record)
    source_path = artifact_root / declared_path
    if not source_path.resolve().is_relative_to(artifact_root.resolve()):
        raise ValueError(
            f"Deployment record reference {job.deployment_record!r} in {job_path} "
            "resolves outside its artifact directory."
        )
    if not source_path.is_file():
        return None

    payload = _load_json(source_path)
    if not _looks_like_deployment_record(payload):
        raise ValueError(
            f"Deployment record {job.deployment_record!r} referenced by {job_path} is not a "
            f"valid deployment record file: {source_path}"
        )

    record = dict(payload)
    if record.get("agent_name") != job.agent_name:
        raise ValueError(
            f"Deployment record {source_path} does not match job evidence agent_name "
            f"{job.agent_name!r}."
        )
    record_agent_kind = record.get("agent_kind")
    if (
        isinstance(record_agent_kind, str)
        and record_agent_kind.strip()
        and record_agent_kind != job.agent_kind
    ):
        raise ValueError(
            f"Deployment record {source_path} agent_kind {record_agent_kind!r} does not "
            f"match job evidence agent_kind {job.agent_kind!r}."
        )

    if job.agent_kind == "hosted":
        record_reference = record.get("image_reference")
        record_digest = record.get("image_digest")
        hosted_success = record.get("status") == "succeeded"
        if hosted_success and (
            not isinstance(record_reference, str)
            or not record_reference
            or not isinstance(record_digest, str)
            or not record_digest
        ):
            raise ValueError(
                "Successful hosted deployment record "
                f"{source_path} must define non-empty image_reference and image_digest."
            )
        if record_reference is not None and job.image_reference != record_reference:
            raise ValueError(
                f"Deployment record {source_path} image_reference {record_reference!r} "
                f"does not match job evidence image_reference {job.image_reference!r}."
            )
        if record_digest is not None and job.image_digest != record_digest:
            raise ValueError(
                f"Deployment record {source_path} image_digest {record_digest!r} "
                f"does not match job evidence image_digest {job.image_digest!r}."
            )

    return record


def _agent_status(
    job_payload: Mapping[str, Any] | None,
    deployment_record: Mapping[str, Any] | None,
) -> str:
    if job_payload is None:
        return "missing"
    if (
        job_payload.get("job_status") == "failed"
        or job_payload.get("image_build_outcome") == "failed"
        or job_payload.get("deploy_outcome") == "failed"
    ):
        return "failed"

    if deployment_record is not None and deployment_record.get("status") == "failed":
        return "failed"

    deploy_outcome = job_payload.get("deploy_outcome")
    if deploy_outcome == "succeeded":
        if deployment_record is None:
            return "incomplete"
        if deployment_record.get("status") == "succeeded":
            return "succeeded"
        return "incomplete"

    if deployment_record is None:
        return "incomplete"
    return "incomplete"


def build_release_manifest(
    config: AutomationConfig,
    evidence_root: Path,
    metadata: ReleaseMetadata,
    selected_agents: tuple[str, ...],
) -> dict[str, object]:
    if metadata.environment not in config.environments:
        available = ", ".join(sorted(config.environments))
        raise ValueError(
            f"Unknown environment {metadata.environment!r}. Available: {available}"
        )

    if not selected_agents:
        raise ValueError("selected_agents cannot be empty.")
    if len(set(selected_agents)) != len(selected_agents):
        raise ValueError("selected_agents contains duplicates.")

    known_agents = {agent.name: agent for agent in config.agents}
    unknown = [name for name in selected_agents if name not in known_agents]
    if unknown:
        raise ValueError(f"selected_agents contains unknown names: {sorted(unknown)}")

    job_by_agent = _collect_evidence(evidence_root, run_id=metadata.run_id)
    unknown_job_agents = sorted(set(job_by_agent) - set(known_agents))
    if unknown_job_agents:
        raise ValueError(
            "Job evidence contains unknown agent_name values: "
            f"{unknown_job_agents}"
        )

    agent_rows: list[dict[str, object]] = []
    statuses: list[str] = []
    for name in selected_agents:
        spec = known_agents[name]
        collected_job = job_by_agent.get(name)
        job_payload: dict[str, Any] | None = None
        deployment_record: dict[str, Any] | None = None
        evidence_run_attempt: int | None = None

        if collected_job is not None:
            job_path, job_evidence = collected_job
            evidence_run_attempt = job_evidence.run_attempt
            if job_evidence.agent_name != name:
                raise ValueError(
                    f"Job evidence in {job_path} has agent_name {job_evidence.agent_name!r}, "
                    f"expected {name!r}."
                )
            if job_evidence.agent_kind != spec.kind:
                raise ValueError(
                    f"Job evidence in {job_path} has agent_kind {job_evidence.agent_kind!r}, "
                    f"expected {spec.kind!r}."
                )
            if job_evidence.environment != metadata.environment:
                raise ValueError(
                    f"Job evidence in {job_path} has environment "
                    f"{job_evidence.environment!r}, expected {metadata.environment!r}."
                )
            job_payload = {
                "schema_version": JOB_EVIDENCE_SCHEMA_VERSION,
                "kind": JOB_EVIDENCE_KIND,
                **asdict(job_evidence),
            }
            deployment_record = _resolve_deployment_record(
                job=job_evidence, job_path=job_path
            )

        job_row = job_payload if job_payload is not None else {"status": "missing"}
        status = _agent_status(job_payload, deployment_record)
        statuses.append(status)
        agent_rows.append(
            {
                "agent_name": name,
                "agent_kind": spec.kind,
                "status": status,
                "evidence_run_attempt": evidence_run_attempt,
                "evidence_is_current_attempt": (
                    evidence_run_attempt is not None
                    and str(evidence_run_attempt) == metadata.run_attempt
                ),
                "job_evidence": job_row,
                "deployment_record": deployment_record,
            }
        )

    if metadata.deployment_result in {"failure", "cancelled"} or any(
        status == "failed" for status in statuses
    ):
        overall_status = "failed"
    elif metadata.deployment_result != "success" or any(
        status in {"missing", "incomplete"} for status in statuses
    ):
        overall_status = "incomplete"
    else:
        overall_status = "succeeded"

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": overall_status,
        "workflow_url": metadata.workflow_url,
        "repository": metadata.repository,
        "ref": metadata.ref,
        "environment": metadata.environment,
        "commit": metadata.commit,
        "actor": metadata.actor,
        "event": metadata.event,
        "run_id": metadata.run_id,
        "run_attempt": metadata.run_attempt,
        "deployment_result": metadata.deployment_result,
        "selected_agents": list(selected_agents),
        "monitoring_url": metadata.monitoring_url,
        "generated_at": metadata.generated_at,
        "agents": agent_rows,
    }


def _default_workflow_url(environ: Mapping[str, str]) -> str | None:
    explicit = environ.get("GITHUB_WORKFLOW_URL")
    if explicit:
        return explicit

    repository = environ.get("GITHUB_REPOSITORY")
    run_id = environ.get("GITHUB_RUN_ID")
    if not repository or not run_id:
        return None
    host = environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{host}/{repository}/actions/runs/{run_id}"


def _add_job_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "job",
        help="Write per-agent pipeline evidence JSON.",
        allow_abbrev=False,
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--agent-kind", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--job-status", required=True)
    parser.add_argument("--image-build-outcome", required=True)
    parser.add_argument("--deploy-outcome", required=True)
    parser.add_argument("--image-reference")
    parser.add_argument("--image-digest")
    parser.add_argument("--deployment-record")
    parser.set_defaults(command="job")


def _add_manifest_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "manifest",
        help="Build aggregate release manifest JSON.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--agents", required=True)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--monitoring-url", default=os.environ.get("FOUNDRY_MONITORING_URL"))
    parser.add_argument("--workflow-url", default=_default_workflow_url(os.environ))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF"))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR"))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    parser.add_argument(
        "--deployment-result", required=True, choices=sorted(DEPLOYMENT_RESULTS)
    )
    parser.add_argument(
        "--generated-at",
        default=datetime.now(UTC).isoformat(),
        help="UTC timestamp for manifest generation (ISO-8601).",
    )
    parser.set_defaults(command="manifest")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="release-evidence",
        description="Write per-job evidence and aggregate release manifests.",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_job_parser(subparsers)
    _add_manifest_parser(subparsers)
    parsed = parser.parse_args(args)

    try:
        if parsed.command == "job":
            write_job_evidence(
                parsed.output,
                JobEvidence(
                    agent_name=parsed.agent_name,
                    agent_kind=parsed.agent_kind,
                    environment=parsed.environment,
                    run_id=parsed.run_id,
                    run_attempt=parsed.run_attempt,
                    job_status=parsed.job_status,
                    image_build_outcome=parsed.image_build_outcome,
                    deploy_outcome=parsed.deploy_outcome,
                    image_reference=parsed.image_reference,
                    image_digest=parsed.image_digest,
                    deployment_record=parsed.deployment_record,
                ),
            )
            return

        config = load_automation_config(parsed.config)
        selected = tuple(agent.name for agent in select_agents(config, parsed.agents))
        manifest = build_release_manifest(
            config=config,
            evidence_root=parsed.evidence_root,
            metadata=ReleaseMetadata(
                environment=parsed.environment,
                workflow_url=parsed.workflow_url,
                repository=parsed.repository,
                ref=parsed.ref,
                commit=parsed.commit,
                actor=parsed.actor,
                event=parsed.event,
                run_id=parsed.run_id,
                run_attempt=parsed.run_attempt,
                deployment_result=parsed.deployment_result,
                monitoring_url=parsed.monitoring_url,
                generated_at=parsed.generated_at,
            ),
            selected_agents=selected,
        )
        _write_json_atomic(parsed.output, manifest)
    except (AutomationConfigError, ValueError, OSError) as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")


__all__ = [
    "JobEvidence",
    "ReleaseMetadata",
    "build_release_manifest",
    "main",
    "write_job_evidence",
]
