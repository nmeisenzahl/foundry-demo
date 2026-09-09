import json
from pathlib import Path

import pytest

from foundry_demo.delivery.records import (
    AuditMetadata,
    DeploymentPhase,
    DeploymentRecorder,
    collect_audit_metadata,
)
from foundry_demo.delivery.smoke import SmokeEvidence


def test_audit_metadata_from_github_env() -> None:
    env = {
        "GITHUB_SHA": "abc1234",
        "GITHUB_REPOSITORY": "octocat/repo",
        "GITHUB_RUN_ID": "98765",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    meta = collect_audit_metadata(env)
    assert meta.git_sha == "abc1234"
    assert meta.repository == "octocat/repo"
    assert meta.run_id == "98765"
    assert meta.run_attempt == "1"
    assert meta.workflow_url == "https://github.com/octocat/repo/actions/runs/98765"
    assert meta.git_dirty is None


def test_audit_metadata_local_commitless_git(tmp_path: Path) -> None:
    # An empty directory has no git repo or commits -> git_sha is None, no error raised
    meta = collect_audit_metadata({}, repository_root=tmp_path)
    assert meta.git_sha is None
    assert meta.repository is None
    assert meta.workflow_url is None


def test_recorder_success_record_shape(tmp_path: Path) -> None:
    records_dir = tmp_path / "artifacts" / "deployments"
    audit = AuditMetadata(
        git_sha="deadbeef",
        repository="org/repo",
        workflow_url="https://github.com/org/repo/actions/runs/1",
        run_id="1",
        run_attempt="1",
        git_dirty=True,
    )

    with DeploymentRecorder(
        agent_name="research-assistant",
        record_dir=records_dir,
        audit_metadata=audit,
    ) as recorder:
        recorder.set_project_endpoint(
            "https://example.services.ai.azure.com/api/projects/example-dev"
        )
        recorder.set_agent_kind("prompt")
        recorder.set_phase(DeploymentPhase.resolving_current)
        recorder.set_previous_active_version("1")
        recorder.set_phase(DeploymentPhase.candidate_created)
        recorder.set_candidate_version("2")
        recorder.set_artifact("prompt_definition", "research-assistant", "a" * 64)
        recorder.set_smoke_evidence(
            SmokeEvidence(
                response_id="resp-123",
                response_status="completed",
                output_text_present=True,
                output_item_counts={"web_search_call": 1, "message": 1},
                completed_output_item_counts={"web_search_call": 1},
                annotation_counts={"url_citation": 1},
            )
        )
        recorder.set_phase(DeploymentPhase.cutover_complete)
        recorder.succeed()

    assert recorder.record_path.is_file()
    # Check no temp files left behind
    assert list(records_dir.glob("*.tmp*")) == []

    content = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert content["schema_version"] == "4"
    assert content["agent_name"] == "research-assistant"
    assert content["project_endpoint"] == (
        "https://example.services.ai.azure.com/api/projects/example-dev"
    )
    assert content["candidate_version"] == "2"
    assert content["previous_active_version"] == "1"
    assert content["definition_sha256"] == "a" * 64
    assert content["image_reference"] is None
    assert content["image_digest"] is None
    assert content["status"] == "succeeded"
    assert content["phase"] == "cutover_complete"
    assert content["failure_message"] is None
    assert content["git_sha"] == "deadbeef"
    assert content["git_dirty"] is True
    assert content["smoke"] == {
        "response_id": "resp-123",
        "response_status": "completed",
        "output_text_present": True,
        "output_item_counts": {"web_search_call": 1, "message": 1},
        "completed_output_item_counts": {"web_search_call": 1},
        "annotation_counts": {"url_citation": 1},
        "completed_tool_name_counts": {},
        "source_domain_counts": {},
    }

    # Verify no prompt or response_body keys
    assert "prompt" not in content
    assert "response_body" not in content
    assert "output_text" not in content


def test_recorder_exception_writes_failed_record_and_propagates(tmp_path: Path) -> None:
    records_dir = tmp_path / "records"

    with (
        pytest.raises(ValueError, match="simulated failure"),
        DeploymentRecorder(
            agent_name="research-assistant",
            record_dir=records_dir,
            audit_metadata=AuditMetadata(None, None, None, None, None),
        ) as recorder,
    ):
        recorder.set_phase(DeploymentPhase.candidate_created)
        recorder.set_candidate_version("3")
        raise ValueError("simulated failure")

    assert recorder.record_path.is_file()
    content = json.loads(recorder.record_path.read_text(encoding="utf-8"))
    assert content["status"] == "failed"
    assert content["phase"] == "candidate_created"
    assert "simulated failure" in content["failure_message"]
    assert content["candidate_version"] == "3"
