"""Deployment records, phases, and audit metadata tracking."""

import json
import os
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from foundry_demo.delivery.smoke import SmokeEvidence


class DeploymentPhase(StrEnum):
    """Phases of an immutable prompt agent deployment."""

    initializing = "initializing"
    resolving_current = "resolving_current"
    current_pinned = "current_pinned"
    candidate_created = "candidate_created"
    candidate_ready = "candidate_ready"
    smoke_passed = "smoke_passed"
    cutover_complete = "cutover_complete"


@dataclass(frozen=True)
class AuditMetadata:
    """Optional source and audit metadata for deployment tracking."""

    git_sha: str | None
    repository: str | None
    workflow_url: str | None
    run_id: str | None
    run_attempt: str | None
    git_dirty: bool | None = None


def collect_audit_metadata(
    environ: Mapping[str, str] | None = None,
    repository_root: Path | None = None,
) -> AuditMetadata:
    """Collect GitHub Actions or local Git metadata without failing on missing info."""
    env = os.environ if environ is None else environ

    git_sha = env.get("GITHUB_SHA")
    repository = env.get("GITHUB_REPOSITORY")
    run_id = env.get("GITHUB_RUN_ID")
    run_attempt = env.get("GITHUB_RUN_ATTEMPT")
    git_dirty: bool | None = None

    workflow_url = None
    if repository and run_id:
        workflow_url = f"https://github.com/{repository}/actions/runs/{run_id}"

    if not git_sha:
        root = (
            repository_root if repository_root is not None else Path.cwd().resolve()
        )
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                if sha:
                    git_sha = sha
                status = subprocess.run(
                    ["git", "status", "--porcelain", "--untracked-files=normal"],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if status.returncode == 0:
                    git_dirty = bool(status.stdout.strip())
        except OSError:
            git_sha = None
            git_dirty = None
    elif env.get("GITHUB_ACTIONS") == "true":
        git_dirty = False

    return AuditMetadata(
        git_sha=git_sha or None,
        repository=repository or None,
        workflow_url=workflow_url,
        run_id=run_id or None,
        run_attempt=run_attempt or None,
        git_dirty=git_dirty,
    )


class DeploymentRecorder:
    """Tracks and records deployment state atomically to a JSON file."""

    def __init__(
        self,
        *,
        agent_name: str,
        record_dir: Path,
        audit_metadata: AuditMetadata | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.record_dir = record_dir
        self.audit_metadata = audit_metadata or AuditMetadata(None, None, None, None, None, None)
        self.deployment_id = str(uuid.uuid4())
        self.started_at = datetime.now(UTC).isoformat()
        self.completed_at: str | None = None
        self._phase = DeploymentPhase.initializing
        self._status = "in_progress"
        self._previous_active_version: str | None = None
        self._candidate_version: str | None = None
        self._definition_sha256: str | None = None
        self._project_endpoint: str | None = None
        self._agent_kind: str | None = None
        self._model_deployment_name: str | None = None
        self._artifact_type: str | None = None
        self._artifact_reference: str | None = None
        self._artifact_sha256: str | None = None
        self._image_reference: str | None = None
        self._image_digest: str | None = None
        self._smoke_evidence: SmokeEvidence | None = None
        self._cutover_outcome: str | None = None
        self._observed_active_version: str | None = None
        self._failure_message: str | None = None
        self._skill_name: str | None = None
        self._skill_version: str | None = None
        self._skill_sha256: str | None = None
        self._toolbox_name: str | None = None
        self._toolbox_version: str | None = None
        self._toolbox_endpoint: str | None = None
        self._toolbox_sha256: str | None = None

        filename_ts = self.started_at.replace(":", "-").replace("+", "_")
        self._record_path = self.record_dir / f"{filename_ts}_{self.deployment_id}.json"

    @property
    def record_path(self) -> Path:
        return self._record_path

    def set_phase(self, phase: DeploymentPhase) -> None:
        self._phase = phase

    def set_previous_active_version(self, version: str | None) -> None:
        self._previous_active_version = version

    def set_candidate_version(self, version: str) -> None:
        self._candidate_version = version

    def set_agent_kind(self, value: str) -> None:
        self._agent_kind = value

    def set_project_endpoint(self, value: str) -> None:
        self._project_endpoint = value

    def set_model_deployment_name(self, value: str) -> None:
        """Record the model this deployment actually ran on.

        For an agent on an admin-connected model this is
        ``<connection>/<model>``, which is the only place the record shows that
        inference left Azure through a gateway. It is a resource name, not a
        secret: no endpoint and no credential.
        """
        self._model_deployment_name = value

    def set_artifact(
        self,
        artifact_type: str,
        reference: str,
        sha256: str,
        *,
        image_reference: str | None = None,
        image_digest: str | None = None,
    ) -> None:
        self._artifact_type = artifact_type
        self._artifact_reference = reference
        self._artifact_sha256 = sha256
        self._image_reference = image_reference
        self._image_digest = image_digest
        if artifact_type in {"prompt_definition", "hosted_definition"}:
            self._definition_sha256 = sha256

    def version_metadata(self) -> dict[str, str]:
        metadata = {"deployment_id": self.deployment_id}
        if self.audit_metadata.git_sha:
            metadata["git_sha"] = self.audit_metadata.git_sha
        if self.audit_metadata.repository:
            metadata["repository"] = self.audit_metadata.repository
        if self.audit_metadata.run_id:
            metadata["run_id"] = self.audit_metadata.run_id
        if self.audit_metadata.run_attempt:
            metadata["run_attempt"] = self.audit_metadata.run_attempt
        if self.audit_metadata.workflow_url:
            metadata["workflow_url"] = self.audit_metadata.workflow_url
        if self.audit_metadata.git_dirty is not None:
            metadata["git_dirty"] = str(self.audit_metadata.git_dirty).lower()
        return metadata

    def set_smoke_evidence(self, evidence: SmokeEvidence) -> None:
        self._smoke_evidence = evidence

    def set_cutover_outcome(self, value: str) -> None:
        self._cutover_outcome = value

    def set_foundry_dependencies(self, candidate: Any) -> None:
        self._skill_name = getattr(candidate, "skill_name", None)
        self._skill_version = getattr(candidate, "skill_version", None)
        self._skill_sha256 = getattr(candidate, "skill_sha256", None)
        self._toolbox_name = getattr(candidate, "toolbox_name", None)
        self._toolbox_version = getattr(candidate, "toolbox_version", None)
        self._toolbox_endpoint = getattr(candidate, "toolbox_endpoint", None)
        self._toolbox_sha256 = getattr(candidate, "toolbox_sha256", None)

    def set_observed_active_version(self, version: str | None) -> None:
        self._observed_active_version = version

    def succeed(self) -> None:
        self._status = "succeeded"

    def _write_record(self) -> None:
        self.completed_at = datetime.now(UTC).isoformat()
        self.record_dir.mkdir(parents=True, exist_ok=True)

        smoke_dict: dict[str, Any] | None = None
        if self._smoke_evidence is not None:
            smoke_dict = asdict(self._smoke_evidence)

        data: dict[str, Any] = {
            "schema_version": "5",
            "deployment_id": self.deployment_id,
            "agent_name": self.agent_name,
            "project_endpoint": self._project_endpoint,
            "agent_kind": self._agent_kind,
            "model_deployment_name": self._model_deployment_name,
            "candidate_version": self._candidate_version,
            "previous_active_version": self._previous_active_version,
            "artifact_type": self._artifact_type,
            "artifact_reference": self._artifact_reference,
            "artifact_sha256": self._artifact_sha256,
            "definition_sha256": self._definition_sha256,
            "image_reference": self._image_reference,
            "image_digest": self._image_digest,
            "skill_name": self._skill_name,
            "skill_version": self._skill_version,
            "skill_sha256": self._skill_sha256,
            "toolbox_name": self._toolbox_name,
            "toolbox_version": self._toolbox_version,
            "toolbox_endpoint": self._toolbox_endpoint,
            "toolbox_sha256": self._toolbox_sha256,
            "git_sha": self.audit_metadata.git_sha,
            "repository": self.audit_metadata.repository,
            "workflow_url": self.audit_metadata.workflow_url,
            "run_id": self.audit_metadata.run_id,
            "run_attempt": self.audit_metadata.run_attempt,
            "git_dirty": self.audit_metadata.git_dirty,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "phase": self._phase.value,
            "status": self._status,
            "smoke": smoke_dict,
            "cutover_outcome": self._cutover_outcome,
            "observed_active_version": self._observed_active_version,
            "failure_message": self._failure_message,
        }

        temp_path = self.record_dir / f".tmp_{self.deployment_id}_{uuid.uuid4().hex}.json"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, self._record_path)

    def __enter__(self) -> "DeploymentRecorder":
        self.record_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is not None:
            self._status = "failed"
            self._failure_message = f"{exc_type.__name__}: {exc_val}"
            self._write_record()
            return False
        if self._status != "succeeded":
            self._status = "failed"
            self._failure_message = "Deployment completed without calling succeed()."

        self._write_record()
        return False
