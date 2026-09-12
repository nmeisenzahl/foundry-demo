import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from foundry_demo.agents.architecture_advisor import ARCHITECTURE_ADVISOR_SPEC
from foundry_demo.delivery.automation import load_automation_config
from foundry_demo.delivery.config import DeploymentConfig
from foundry_demo.delivery.evidence import (
    JobEvidence,
    ReleaseMetadata,
    build_release_manifest,
    main,
    write_job_evidence,
)
from foundry_demo.delivery.hosted import HostedAgentOperations


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _config():
    return load_automation_config(_repository_root() / ".github/deployment/config.json")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_deployment_record(
    path: Path,
    *,
    deployment_id: str,
    agent_name: str,
    status: str = "succeeded",
    agent_kind: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": "4",
        "deployment_id": deployment_id,
        "agent_name": agent_name,
        "status": status,
        "phase": "cutover_complete",
        "started_at": "2026-09-09T16:00:00+00:00",
        "completed_at": "2026-09-09T16:05:00+00:00",
    }
    if agent_kind is not None:
        payload["agent_kind"] = agent_kind
    _write_json(path, payload)


def _default_metadata() -> ReleaseMetadata:
    return ReleaseMetadata(
        environment="dev",
        workflow_url="https://github.com/octocat/foundry-demo/actions/runs/42",
        repository="octocat/foundry-demo",
        ref="refs/heads/main",
        commit="abc1234",
        actor="octocat",
        event="workflow_dispatch",
        run_id="42",
        run_attempt="2",
        deployment_result="success",
        monitoring_url="https://portal.azure.com/#monitor",
        generated_at="2026-09-09T17:00:00+00:00",
    )


def _immutable_image_reference(repository: str, digest: str) -> str:
    return f"{repository}@{digest}"


def _workflow_step_script(workflow: str, step_name: str) -> str:
    source = (_repository_root() / ".github/workflows" / workflow).read_text(encoding="utf-8")
    step = source.split(f"      - name: {step_name}\n", 1)[1].split("      - name: ", 1)[0]
    return dedent(step.split("        run: |\n", 1)[1])


def _run_workflow_step(
    workflow: str,
    step_name: str,
    cwd: Path,
    env: dict[str, str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    # Run the actual workflow shell with the installed CLI, without syncing a temp project.
    runner = """
    uv() {
      test "$1" = run && test "$2" = release-evidence || return 1
      shift 2
      "$TEST_PYTHON" -c 'from foundry_demo.delivery.evidence import main; main()' "$@"
    }
    """
    return subprocess.run(
        [
            "bash",
            "-e",
            "-o",
            "pipefail",
            "-c",
            dedent(runner) + _workflow_step_script(workflow, step_name),
        ],
        cwd=cwd,
        env={**os.environ, "TEST_PYTHON": sys.executable, **env},
        capture_output=True,
        text=True,
        check=check,
    )


def _write_attempt(
    evidence_root: Path,
    *,
    run_attempt: int,
    run_id: str = "42",
    agent_name: str = "research-assistant",
    status: str = "succeeded",
    with_record: bool = True,
) -> Path:
    artifact_root = evidence_root / f"deployment-{run_id}-{agent_name}-attempt-{run_attempt}"
    job_path = artifact_root / "evidence" / f"{agent_name}.json"
    hosted = agent_name == "architecture-advisor"
    digest = "sha256:" + "a" * 64 if hosted else None
    reference = f"example.azurecr.io/{agent_name}@{digest}" if hosted else None
    write_job_evidence(
        job_path,
        JobEvidence(
            agent_name=agent_name,
            agent_kind="hosted" if hosted else "prompt",
            environment="dev",
            run_id=run_id,
            run_attempt=run_attempt,
            job_status=status,
            image_build_outcome="succeeded" if hosted else "not_applicable",
            deploy_outcome=status,
            image_reference=reference,
            image_digest=digest,
            deployment_record=f"deployments/{agent_name}.json",
        ),
    )
    if with_record:
        _write_json(
            artifact_root / "deployments" / f"{agent_name}.json",
            {
                "schema_version": "4",
                "deployment_id": f"{agent_name}-{run_attempt}",
                "agent_name": agent_name,
                "agent_kind": "hosted" if hosted else "prompt",
                "status": status,
                "run_id": run_id,
                "run_attempt": str(run_attempt),
                "image_reference": reference,
                "image_digest": digest,
            },
        )
    return job_path


def test_workflow_exports_tagged_cli_image_and_canonical_evidence(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    tagged = "example.azurecr.io/architecture-advisor:run-42-attempt-2"
    github_env = tmp_path / "github-env"
    _run_workflow_step(
        "_deploy-agent.yml",
        "Export hosted image identity",
        tmp_path,
        {
            "IMAGE_TAGGED_REFERENCE": tagged,
            "IMAGE_REPOSITORY_REFERENCE": "example.azurecr.io/architecture-advisor",
            "IMAGE_DIGEST": digest,
            "IMAGE_REFERENCE_ENV_NAME": ARCHITECTURE_ADVISOR_SPEC.image_env_var,
            "IMAGE_DIGEST_ENV_NAME": ARCHITECTURE_ADVISOR_SPEC.image_digest_env_var,
            "GITHUB_ENV": str(github_env),
        },
    )
    exported = dict(line.split("=", 1) for line in github_env.read_text().splitlines())
    config = DeploymentConfig(
        project_endpoint="https://example.services.ai.azure.com/api/projects/dev",
        model_deployment_name="model",
        temperature=None,
        environ=exported,
    )
    assert config.get_hosted_image(ARCHITECTURE_ADVISOR_SPEC).reference == tagged
    project = MagicMock()
    project.agents.create_version.return_value = SimpleNamespace(version="7")
    candidate = HostedAgentOperations().create_candidate(
        project, replace(ARCHITECTURE_ADVISOR_SPEC, toolbox=None), config, {}
    )
    assert candidate.image_reference == exported["DEPLOYMENT_IMAGE_REFERENCE"]
    assert candidate.image_reference == f"example.azurecr.io/architecture-advisor@{digest}"
    assert candidate.image_digest == exported["DEPLOYMENT_IMAGE_DIGEST"] == digest


@pytest.mark.parametrize("with_record", [False, True])
def test_workflow_evidence_matches_uploaded_artifact_layout(
    tmp_path: Path, with_record: bool
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record_path_file = tmp_path / "record-path.txt"
    if with_record:
        _write_deployment_record(
            workspace / "artifacts/deployments/research.json",
            deployment_id="uploaded-record",
            agent_name="research-assistant",
        )
        record_path_file.write_text(
            "artifacts/deployments/research.json\n", encoding="utf-8"
        )
    result = _run_workflow_step(
        "_deploy-agent.yml",
        "Write deployment evidence",
        workspace,
        {
            "AGENT_KIND": "prompt",
            "AGENT_NAME": "research-assistant",
            "ENVIRONMENT": "dev",
            "JOB_STATUS_RAW": "success" if with_record else "failure",
            "IMAGE_BUILD_OUTCOME_RAW": "skipped",
            "DEPLOY_OUTCOME_RAW": "success" if with_record else "skipped",
            "RELEASE_RUN_ID": "42",
            "RELEASE_RUN_ATTEMPT": "1",
            "RECORD_PATH_FILE": str(record_path_file),
        },
    )
    evidence_root = tmp_path / "downloaded"
    artifact_root = evidence_root / "deployment-42-research-assistant-attempt-1"
    shutil.copytree(workspace / "artifacts", artifact_root)
    payload = json.loads((artifact_root / "evidence/research-assistant.json").read_text())
    assert payload["deployment_record"] == ("deployments/research.json" if with_record else None)
    manifest = build_release_manifest(
        _config(), evidence_root, _default_metadata(), ("research-assistant",)
    )
    assert manifest["status"] == ("succeeded" if with_record else "failed")
    record = manifest["agents"][0]["deployment_record"]
    if with_record:
        assert record["deployment_id"] == "uploaded-record"
    else:
        assert record is None
        assert "::warning::No deployment record" in result.stdout


@pytest.mark.parametrize("monitoring_url", ["", "https://portal.azure.com/#monitor"])
def test_workflow_manifest_accepts_optional_repository_monitoring_url(
    tmp_path: Path, monitoring_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FOUNDRY_MONITORING_URL", raising=False)
    output = tmp_path / "manifest.json"
    _run_workflow_step(
        "deploy-agents.yml",
        "Generate release manifest",
        _repository_root(),
        {
            "DEPLOYMENT_SHA": "abc1234",
            "SELECTED_AGENTS": "research-assistant",
            "EVIDENCE_ROOT": str(tmp_path / "missing"),
            "MANIFEST_PATH": str(output),
            "MONITORING_URL": monitoring_url,
            "WORKFLOW_URL": "https://github.com/octocat/foundry-demo/actions/runs/42",
            "RELEASE_REPOSITORY": "octocat/foundry-demo",
            "RELEASE_REF": "refs/heads/main",
            "RELEASE_ACTOR": "octocat",
            "RELEASE_EVENT": "workflow_dispatch",
            "RELEASE_RUN_ID": "42",
            "RELEASE_RUN_ATTEMPT": "2",
            "DEPLOYMENT_RESULT": "success",
        },
    )
    assert json.loads(output.read_text())["monitoring_url"] == (monitoring_url or None)


@pytest.mark.parametrize(
    ("deployment_result", "expected_status"),
    [
        ("success", "succeeded"),
        ("failure", "failed"),
        ("cancelled", "failed"),
        ("skipped", "incomplete"),
    ],
)
@pytest.mark.parametrize("evidence_attempt", [1, 2])
def test_workflow_manifest_and_summary_respect_current_matrix_result(
    tmp_path: Path, deployment_result: str, expected_status: str, evidence_attempt: int
) -> None:
    evidence_root = tmp_path / "evidence root"
    _write_attempt(evidence_root, run_attempt=evidence_attempt)
    output = tmp_path / "release manifest.json"
    summary_path = tmp_path / "release summary.md"
    env = {
        "DEPLOYMENT_SHA": "abc1234",
        "SELECTED_AGENTS": "research-assistant",
        "EVIDENCE_ROOT": str(evidence_root),
        "MANIFEST_PATH": str(output),
        "MONITORING_URL": "https://portal.azure.com/#monitor",
        "WORKFLOW_URL": "https://github.com/octocat/foundry-demo/actions/runs/42",
        "RELEASE_REPOSITORY": "octocat/foundry-demo",
        "RELEASE_REF": "refs/heads/main",
        "RELEASE_ACTOR": "octocat",
        "RELEASE_EVENT": "workflow_dispatch",
        "RELEASE_RUN_ID": "42",
        "RELEASE_RUN_ATTEMPT": "2",
        "RELEASE_SERVER_URL": "https://github.com",
        "DEPLOYMENT_RESULT": deployment_result,
        "MANIFEST_ARTIFACT_NAME": "release-manifest-42-2",
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }
    _run_workflow_step(
        "deploy-agents.yml", "Generate release manifest", _repository_root(), env
    )
    _run_workflow_step(
        "deploy-agents.yml", "Write release summary", _repository_root(), env
    )
    manifest = json.loads(output.read_text())
    summary = summary_path.read_text()
    assert manifest["status"] == expected_status
    assert f"- Overall status: `{expected_status}`" in summary
    assert manifest["deployment_result"] == deployment_result
    assert f"- Current deployment matrix result: `{deployment_result}`" in summary
    agent = manifest["agents"][0]
    assert agent["evidence_run_attempt"] == evidence_attempt
    assert agent["evidence_is_current_attempt"] is (evidence_attempt == 2)
    assert agent["job_evidence"]["run_attempt"] == evidence_attempt
    assert agent["deployment_record"]["run_attempt"] == str(evidence_attempt)
    if evidence_attempt == 1:
        assert "1 (previous)" in summary
        if deployment_result != "success":
            assert "| previous-attempt evidence (succeeded) |" in summary
            assert "| research-assistant | succeeded |" not in summary
        else:
            assert "| research-assistant | succeeded |" in summary
    else:
        assert "2 (current)" in summary
        assert "previous-attempt evidence" not in summary
    if expected_status == "succeeded":
        gate = _run_workflow_step(
            "deploy-agents.yml", "Enforce release status", _repository_root(), env
        )
        assert "Release status: succeeded" in gate.stdout
    else:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            _run_workflow_step(
                "deploy-agents.yml", "Enforce release status", _repository_root(), env
            )
        assert exc.value.returncode == 1
        assert f"Release status is {expected_status}." in exc.value.stdout
        assert f"Deployment matrix result: {deployment_result}" in exc.value.stdout


def test_workflow_release_gate_fails_without_a_manifest(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _run_workflow_step(
            "deploy-agents.yml",
            "Enforce release status",
            _repository_root(),
            {
                "MANIFEST_PATH": str(tmp_path / "missing.json"),
                "DEPLOYMENT_RESULT": "failure",
            },
        )
    assert exc.value.returncode == 1
    assert "No release manifest was generated." in exc.value.stdout
    assert "Deployment matrix result: failure" in exc.value.stdout


def test_workflow_release_gate_fails_when_an_agent_is_incomplete(tmp_path: Path) -> None:
    _write_attempt(tmp_path / "evidence", run_attempt=2, with_record=False)
    manifest = build_release_manifest(
        _config(), tmp_path / "evidence", _default_metadata(), ("research-assistant",)
    )
    assert manifest["status"] == "incomplete"
    output = tmp_path / "manifest.json"
    _write_json(output, manifest)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _run_workflow_step(
            "deploy-agents.yml",
            "Enforce release status",
            _repository_root(),
            {"MANIFEST_PATH": str(output), "DEPLOYMENT_RESULT": "success"},
        )
    assert exc.value.returncode == 1
    assert "Release status is incomplete." in exc.value.stdout


@pytest.mark.parametrize("deployment_result", ["green", "succeeded", "failed", "SUCCESS", ""])
def test_release_metadata_rejects_invalid_deployment_result(deployment_result: str) -> None:
    with pytest.raises(ValueError, match="deployment_result"):
        replace(_default_metadata(), deployment_result=deployment_result)


@pytest.mark.parametrize("deployment_result", ["success", "failure", "cancelled", "skipped"])
def test_release_metadata_normalizes_deployment_result(deployment_result: str) -> None:
    metadata = replace(_default_metadata(), deployment_result=f" {deployment_result} ")
    assert metadata.deployment_result == deployment_result


@pytest.mark.parametrize("deployment_result", [None, "", "succeeded", "canceled"])
def test_manifest_command_requires_valid_explicit_deployment_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], deployment_result: str | None
) -> None:
    output = tmp_path / "manifest.json"
    args = [
        "manifest",
        "--config",
        str(_repository_root() / ".github/deployment/config.json"),
        "--environment",
        "dev",
        "--agents",
        "research-assistant",
        "--evidence-root",
        str(tmp_path / "missing"),
        "--output",
        str(output),
    ]
    if deployment_result is not None:
        args.extend(["--deployment-result", deployment_result])
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert "--deployment-result" in capsys.readouterr().err
    assert not output.exists()


def test_workflow_passes_current_matrix_result_via_environment() -> None:
    workflow = (_repository_root() / ".github/workflows/deploy-agents.yml").read_text()
    step = workflow.split("      - name: Generate release manifest\n", 1)[1].split(
        "      - name: ", 1
    )[0]
    assert "DEPLOYMENT_RESULT: ${{ needs.deploy.result }}" in step
    script = _workflow_step_script("deploy-agents.yml", "Generate release manifest")
    assert '--deployment-result "$DEPLOYMENT_RESULT"' in script
    assert "${{" not in script


@pytest.mark.parametrize("deployment_result", ["failure", "cancelled", "skipped"])
def test_workflow_summary_never_overrides_current_matrix_failure_with_manifest_success(
    tmp_path: Path, deployment_result: str
) -> None:
    _write_attempt(tmp_path / "evidence", run_attempt=1)
    manifest = build_release_manifest(
        _config(), tmp_path / "evidence", _default_metadata(), ("research-assistant",)
    )
    assert manifest["status"] == "succeeded"
    output = tmp_path / "manifest.json"
    _write_json(output, manifest)
    summary_path = tmp_path / "summary.md"
    _run_workflow_step(
        "deploy-agents.yml",
        "Write release summary",
        _repository_root(),
        {
            "MANIFEST_PATH": str(output),
            "RELEASE_SERVER_URL": "https://github.com",
            "RELEASE_REPOSITORY": "octocat/foundry-demo",
            "RELEASE_RUN_ID": "42",
            "MONITORING_URL": "",
            "MANIFEST_ARTIFACT_NAME": "release-manifest-42-2",
            "DEPLOYMENT_RESULT": deployment_result,
            "GITHUB_STEP_SUMMARY": str(summary_path),
        },
    )
    summary = summary_path.read_text()
    assert f"- Overall status: `{deployment_result}`" in summary
    assert "- Overall status: `succeeded`" not in summary
    assert "| previous-attempt evidence (succeeded) | 1 (previous) |" in summary


def test_manifest_reuses_prior_attempt_for_agent_not_rerun(tmp_path: Path) -> None:
    _write_attempt(tmp_path, run_attempt=1)
    _write_attempt(tmp_path, run_attempt=1, agent_name="architecture-advisor", status="failed")
    _write_attempt(tmp_path, run_attempt=2, agent_name="architecture-advisor")
    manifest = build_release_manifest(
        _config(), tmp_path, _default_metadata(), ("architecture-advisor", "research-assistant")
    )
    assert manifest["status"] == "succeeded"
    assert manifest["deployment_result"] == "success"
    assert manifest["run_attempt"] == "2"
    hosted, prompt = manifest["agents"]
    assert hosted["evidence_run_attempt"] == 2
    assert hosted["evidence_is_current_attempt"] is True
    assert hosted["job_evidence"]["run_attempt"] == 2
    assert prompt["evidence_run_attempt"] == 1
    assert prompt["evidence_is_current_attempt"] is False
    assert prompt["job_evidence"]["run_attempt"] == 1
    assert prompt["deployment_record"]["run_attempt"] == "1"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_manifest_selects_highest_numeric_attempt_regardless_of_status(
    tmp_path: Path, status: str
) -> None:
    _write_attempt(tmp_path, run_attempt=2)
    _write_attempt(tmp_path, run_attempt=10, status=status)
    manifest = build_release_manifest(
        _config(),
        tmp_path,
        replace(_default_metadata(), run_attempt="10"),
        ("research-assistant",),
    )
    assert manifest["status"] == status
    job = manifest["agents"][0]["job_evidence"]
    assert job["run_id"] == "42"
    assert job["run_attempt"] == 10
    assert manifest["agents"][0]["deployment_record"]["deployment_id"] == "research-assistant-10"


def test_manifest_rejects_duplicate_older_attempt_after_newer_attempt(tmp_path: Path) -> None:
    _write_attempt(tmp_path, run_attempt=10)
    older = _write_attempt(tmp_path, run_attempt=2)
    shutil.copyfile(older, older.with_name("duplicate.json"))
    with pytest.raises(ValueError, match="Duplicate job evidence"):
        build_release_manifest(
            _config(),
            tmp_path,
            replace(_default_metadata(), run_attempt="10"),
            ("research-assistant",),
        )


@pytest.mark.parametrize("foreign_attempt", [1, 3])
def test_manifest_rejects_foreign_run_even_when_not_selected(
    tmp_path: Path, foreign_attempt: int
) -> None:
    _write_attempt(tmp_path, run_attempt=2)
    _write_attempt(
        tmp_path, run_attempt=foreign_attempt, run_id="99", agent_name="architecture-advisor"
    )
    with pytest.raises(ValueError, match="run_id"):
        build_release_manifest(
            _config(), tmp_path, _default_metadata(), ("research-assistant",)
        )


def test_manifest_does_not_borrow_missing_record_from_prior_attempt(tmp_path: Path) -> None:
    _write_attempt(tmp_path, run_attempt=1)
    _write_attempt(tmp_path, run_attempt=2, with_record=False)
    manifest = build_release_manifest(
        _config(), tmp_path, _default_metadata(), ("research-assistant",)
    )
    assert manifest["status"] == "incomplete"
    assert manifest["agents"][0]["job_evidence"]["run_attempt"] == 2
    assert manifest["agents"][0]["deployment_record"] is None


@pytest.mark.parametrize("run_attempt", [0, -1, True, "2", 1.5, None])
def test_job_evidence_rejects_invalid_run_attempt_payload(
    tmp_path: Path, run_attempt: object
) -> None:
    job_path = _write_attempt(tmp_path, run_attempt=1)
    payload = json.loads(job_path.read_text())
    payload["run_attempt"] = run_attempt
    _write_json(job_path, payload)
    with pytest.raises(ValueError, match="run_attempt"):
        build_release_manifest(
            _config(), tmp_path, _default_metadata(), ("research-assistant",)
        )


@pytest.mark.parametrize("run_id", ["", "  ", None, 42])
def test_job_evidence_rejects_missing_or_invalid_run_id(tmp_path: Path, run_id: object) -> None:
    job_path = _write_attempt(tmp_path, run_attempt=1)
    payload = json.loads(job_path.read_text())
    payload["run_id"] = run_id
    _write_json(job_path, payload)
    with pytest.raises(ValueError, match="run_id"):
        build_release_manifest(
            _config(), tmp_path, _default_metadata(), ("research-assistant",)
        )


@pytest.mark.parametrize("image_fields", [{}, {"image_reference": None, "image_digest": None}])
def test_failed_hosted_record_before_candidate_may_omit_images(
    tmp_path: Path, image_fields: dict[str, object]
) -> None:
    job_path = _write_attempt(
        tmp_path, run_attempt=1, agent_name="architecture-advisor", status="failed"
    )
    record_path = job_path.parent.parent / "deployments/architecture-advisor.json"
    _write_json(
        record_path,
        {
            "schema_version": "4",
            "deployment_id": "pre-candidate-failure",
            "agent_name": "architecture-advisor",
            "agent_kind": "hosted",
            "status": "failed",
            "phase": "current_pinned",
            "candidate_version": None,
            **image_fields,
        },
    )
    manifest = build_release_manifest(
        _config(), tmp_path, _default_metadata(), ("architecture-advisor",)
    )
    assert manifest["status"] == "failed"
    assert manifest["agents"][0]["deployment_record"]["candidate_version"] is None
    assert manifest["agents"][0]["job_evidence"]["image_digest"] == "sha256:" + "a" * 64


@pytest.mark.parametrize("field", ["image_reference", "image_digest"])
@pytest.mark.parametrize("conflicts", [False, True])
def test_failed_hosted_record_checks_each_present_image_field(
    tmp_path: Path, field: str, conflicts: bool
) -> None:
    job_path = _write_attempt(
        tmp_path, run_attempt=1, agent_name="architecture-advisor", status="failed"
    )
    record_path = job_path.parent.parent / "deployments/architecture-advisor.json"
    record = json.loads(record_path.read_text())
    record.pop("image_digest" if field == "image_reference" else "image_reference")
    if conflicts:
        record[field] = "conflicting-image-identity"
    _write_json(record_path, record)
    if conflicts:
        with pytest.raises(ValueError, match=f"does not match job evidence {field}"):
            build_release_manifest(
                _config(), tmp_path, _default_metadata(), ("architecture-advisor",)
            )
    else:
        manifest = build_release_manifest(
            _config(), tmp_path, _default_metadata(), ("architecture-advisor",)
        )
        assert manifest["status"] == "failed"


@pytest.mark.parametrize("field", ["image_reference", "image_digest"])
def test_failed_hosted_record_rejects_identity_absent_from_job(
    tmp_path: Path, field: str
) -> None:
    job_path = _write_attempt(
        tmp_path, run_attempt=1, agent_name="architecture-advisor", status="failed"
    )
    job = json.loads(job_path.read_text())
    job["image_reference"] = None
    job["image_digest"] = None
    _write_json(job_path, job)
    record_path = job_path.parent.parent / "deployments/architecture-advisor.json"
    record = json.loads(record_path.read_text())
    record.pop("image_digest" if field == "image_reference" else "image_reference")
    _write_json(record_path, record)
    with pytest.raises(ValueError, match=f"does not match job evidence {field}"):
        build_release_manifest(
            _config(), tmp_path, _default_metadata(), ("architecture-advisor",)
        )


def test_write_job_evidence_is_atomic_and_schema_stable(tmp_path: Path) -> None:
    output = tmp_path / "downloaded" / "job-evidence.json"
    write_job_evidence(
        output,
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="records/prompt.json",
        ),
    )

    assert output.is_file()
    assert list(output.parent.glob("*.tmp*")) == []
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": "1",
        "kind": "job_evidence",
        "agent_name": "research-assistant",
        "agent_kind": "prompt",
        "environment": "dev",
        "run_id": "42",
        "run_attempt": 1,
        "job_status": "succeeded",
        "image_build_outcome": "not_applicable",
        "deploy_outcome": "succeeded",
        "image_reference": None,
        "image_digest": None,
        "deployment_record": "records/prompt.json",
    }


def test_build_release_manifest_captures_prompt_success(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "prompt.json",
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="deployments/research.json",
        ),
    )
    _write_json(
        evidence_root / "deployments" / "research.json",
        {
            "schema_version": "4",
            "deployment_id": "d1",
            "agent_name": "research-assistant",
            "status": "succeeded",
            "phase": "cutover_complete",
            "workflow_url": "https://github.com/octocat/foundry-demo/actions/runs/42",
            "git_sha": "abc1234",
            "run_id": "42",
            "run_attempt": "1",
            "image_reference": None,
            "image_digest": None,
            "smoke": {"output_text_present": True},
            "started_at": "2026-09-09T16:00:00+00:00",
            "completed_at": "2026-09-09T16:05:00+00:00",
        },
    )

    manifest = build_release_manifest(
        _config(),
        evidence_root,
        _default_metadata(),
        selected_agents=("research-assistant",),
    )

    assert manifest["schema_version"] == "1"
    assert manifest["status"] == "succeeded"
    assert manifest["workflow_url"] == "https://github.com/octocat/foundry-demo/actions/runs/42"
    assert manifest["repository"] == "octocat/foundry-demo"
    assert manifest["ref"] == "refs/heads/main"
    assert manifest["environment"] == "dev"
    assert manifest["commit"] == "abc1234"
    assert manifest["actor"] == "octocat"
    assert manifest["event"] == "workflow_dispatch"
    assert manifest["run_id"] == "42"
    assert manifest["run_attempt"] == "2"
    assert manifest["selected_agents"] == ["research-assistant"]
    assert manifest["monitoring_url"] == "https://portal.azure.com/#monitor"
    assert manifest["generated_at"] == "2026-09-09T17:00:00+00:00"

    agent = manifest["agents"][0]
    assert agent["agent_name"] == "research-assistant"
    assert agent["agent_kind"] == "prompt"
    assert agent["status"] == "succeeded"
    assert agent["deployment_record"]["status"] == "succeeded"
    assert agent["deployment_record"]["image_reference"] is None
    assert agent["deployment_record"]["image_digest"] is None
    assert agent["deployment_record"]["smoke"] == {"output_text_present": True}


def test_build_release_manifest_captures_hosted_image_reference_and_digest(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "artifacts"
    digest = "sha256:" + "a" * 64
    image_reference = _immutable_image_reference(
        "example.azurecr.io/architecture-advisor", digest
    )
    write_job_evidence(
        evidence_root / "job" / "hosted.json",
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="succeeded",
            deploy_outcome="succeeded",
            image_reference=image_reference,
            image_digest=digest,
            deployment_record="deployments/architecture.json",
        ),
    )
    _write_json(
        evidence_root / "deployments" / "architecture.json",
        {
            "schema_version": "4",
            "deployment_id": "d2",
            "agent_name": "architecture-advisor",
            "status": "succeeded",
            "phase": "cutover_complete",
            "image_reference": image_reference,
            "image_digest": digest,
            "started_at": "2026-09-09T16:00:00+00:00",
            "completed_at": "2026-09-09T16:05:00+00:00",
        },
    )

    manifest = build_release_manifest(
        _config(),
        evidence_root,
        _default_metadata(),
        selected_agents=("architecture-advisor",),
    )

    assert manifest["status"] == "succeeded"
    agent = manifest["agents"][0]
    assert agent["status"] == "succeeded"
    assert agent["job_evidence"]["image_reference"] == image_reference
    assert agent["job_evidence"]["image_digest"] == digest
    assert agent["deployment_record"]["image_reference"] == image_reference
    assert agent["deployment_record"]["image_digest"] == digest


@pytest.mark.parametrize(
    ("image_build_outcome", "deploy_outcome"),
    [
        ("failed", "skipped"),
        ("succeeded", "failed"),
    ],
)
def test_build_release_manifest_handles_build_or_auth_failure_without_deployment_record(
    tmp_path: Path,
    image_build_outcome: str,
    deploy_outcome: str,
) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "failed.json",
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="failed",
            image_build_outcome=image_build_outcome,
            deploy_outcome=deploy_outcome,
            image_reference=None,
            image_digest=None,
            deployment_record=None,
        ),
    )

    manifest = build_release_manifest(
        _config(),
        evidence_root,
        _default_metadata(),
        selected_agents=("architecture-advisor",),
    )

    assert manifest["status"] == "failed"
    agent = manifest["agents"][0]
    assert agent["status"] == "failed"
    assert agent["deployment_record"] is None


def test_build_release_manifest_marks_missing_selected_agents(tmp_path: Path) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "research.json",
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="deployments/research.json",
        ),
    )
    _write_json(
        evidence_root / "deployments" / "research.json",
        {
            "schema_version": "4",
            "deployment_id": "d3",
            "agent_name": "research-assistant",
            "status": "succeeded",
            "phase": "cutover_complete",
            "started_at": "2026-09-09T16:00:00+00:00",
            "completed_at": "2026-09-09T16:05:00+00:00",
        },
    )

    manifest = build_release_manifest(
        _config(),
        evidence_root,
        _default_metadata(),
        selected_agents=("architecture-advisor", "research-assistant"),
    )

    assert manifest["status"] == "incomplete"
    by_name = {agent["agent_name"]: agent for agent in manifest["agents"]}
    assert by_name["architecture-advisor"]["status"] == "missing"
    assert by_name["architecture-advisor"]["evidence_run_attempt"] is None
    assert by_name["architecture-advisor"]["evidence_is_current_attempt"] is False
    assert by_name["architecture-advisor"]["job_evidence"] == {"status": "missing"}
    assert by_name["research-assistant"]["status"] == "succeeded"


def test_job_evidence_validates_status_and_hosted_digest() -> None:
    with pytest.raises(ValueError, match="job_status"):
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="green",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record=None,
        )

    with pytest.raises(ValueError, match="image_digest"):
        invalid_digest = "sha256:1234"
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="succeeded",
            deploy_outcome="succeeded",
            image_reference=_immutable_image_reference(
                "example.azurecr.io/architecture-advisor", invalid_digest
            ),
            image_digest=invalid_digest,
            deployment_record="deployments/architecture.json",
        )


@pytest.mark.parametrize(
    ("image_reference", "image_digest"),
    [
        ("example.azurecr.io/architecture-advisor@sha256:" + "a" * 64, None),
        (None, "sha256:" + "a" * 64),
        (None, None),
    ],
)
def test_successful_hosted_job_evidence_requires_reference_and_digest(
    image_reference: str | None,
    image_digest: str | None,
) -> None:
    with pytest.raises(
        ValueError,
        match="Successful hosted job evidence|define image_reference and image_digest together",
    ):
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="succeeded",
            deploy_outcome="succeeded",
            image_reference=image_reference,
            image_digest=image_digest,
            deployment_record="deployments/architecture.json",
        )


def test_successful_hosted_job_evidence_requires_immutable_reference() -> None:
    with pytest.raises(ValueError, match="immutable image_reference"):
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="succeeded",
            deploy_outcome="succeeded",
            image_reference="example.azurecr.io/architecture-advisor:v1",
            image_digest="sha256:" + "a" * 64,
            deployment_record="deployments/architecture.json",
        )


def test_main_job_and_manifest_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence_root = tmp_path / "evidence"
    job_output = evidence_root / "run-1" / "job-hosted.json"
    digest = "sha256:" + "b" * 64
    image_reference = _immutable_image_reference(
        "example.azurecr.io/architecture-advisor", digest
    )
    main(
        [
            "job",
            "--output",
            str(job_output),
            "--agent-name",
            "architecture-advisor",
            "--agent-kind",
            "hosted",
            "--environment",
            "dev",
            "--run-id",
            "201",
            "--run-attempt",
            "3",
            "--job-status",
            "succeeded",
            "--image-build-outcome",
            "succeeded",
            "--deploy-outcome",
            "succeeded",
            "--image-reference",
            image_reference,
            "--image-digest",
            digest,
            "--deployment-record",
            "deployments/architecture.json",
        ]
    )

    _write_json(
        evidence_root / "deployments" / "architecture.json",
        {
            "schema_version": "4",
            "deployment_id": "d4",
            "agent_name": "architecture-advisor",
            "status": "succeeded",
            "phase": "cutover_complete",
            "image_reference": image_reference,
            "image_digest": digest,
            "started_at": "2026-09-09T16:00:00+00:00",
            "completed_at": "2026-09-09T16:05:00+00:00",
        },
    )

    output = tmp_path / "release-manifest.json"
    monkeypatch.setenv("GITHUB_SHA", "feedface")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REPOSITORY", "octocat/foundry-demo")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_RUN_ID", "200")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "3")
    main(
        [
            "manifest",
            "--config",
            str(_repository_root() / ".github/deployment/config.json"),
            "--environment",
            "dev",
            "--agents",
            "architecture-advisor",
            "--evidence-root",
            str(evidence_root),
            "--output",
            str(output),
            "--monitoring-url",
            "https://portal.azure.com/#monitor",
            "--workflow-url",
            "https://github.com/octocat/foundry-demo/actions/runs/201",
            "--repository",
            "octocat/foundry-demo",
            "--ref",
            "refs/heads/main",
            "--run-id",
            "201",
            "--run-attempt",
            "4",
            "--deployment-result",
            "success",
            "--generated-at",
            "2026-09-09T18:00:00+00:00",
        ]
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "succeeded"
    assert manifest["commit"] == "feedface"
    assert manifest["repository"] == "octocat/foundry-demo"
    assert manifest["ref"] == "refs/heads/main"
    assert manifest["actor"] == "octocat"
    assert manifest["event"] == "workflow_dispatch"
    assert manifest["run_id"] == "201"
    assert manifest["run_attempt"] == "4"
    assert manifest["deployment_result"] == "success"
    assert manifest["agents"][0]["evidence_run_attempt"] == 3
    assert manifest["agents"][0]["evidence_is_current_attempt"] is False
    assert manifest["agents"][0]["job_evidence"]["run_id"] == "201"
    assert manifest["agents"][0]["job_evidence"]["run_attempt"] == 3
    assert manifest["workflow_url"] == "https://github.com/octocat/foundry-demo/actions/runs/201"
    assert manifest["generated_at"] == "2026-09-09T18:00:00+00:00"


def test_build_release_manifest_rejects_job_evidence_kind_mismatch(tmp_path: Path) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "architecture.json",
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="deployments/architecture.json",
        ),
    )
    _write_deployment_record(
        evidence_root / "deployments" / "architecture.json",
        deployment_id="mismatch-kind",
        agent_name="architecture-advisor",
    )

    with pytest.raises(ValueError, match="agent_kind"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("architecture-advisor",),
        )


def test_build_release_manifest_rejects_job_evidence_environment_mismatch(tmp_path: Path) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "research.json",
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="prod",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="deployments/research.json",
        ),
    )
    _write_deployment_record(
        evidence_root / "deployments" / "research.json",
        deployment_id="mismatch-env",
        agent_name="research-assistant",
    )

    with pytest.raises(ValueError, match="environment"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("research-assistant",),
        )


def test_build_release_manifest_rejects_invalid_job_evidence_payload(tmp_path: Path) -> None:
    evidence_root = tmp_path / "artifacts"
    _write_json(
        evidence_root / "job" / "broken.json",
        {
            "schema_version": "1",
            "kind": "job_evidence",
            "agent_name": "research-assistant",
            "agent_kind": "prompt",
            # missing required environment and outcome fields
        },
    )

    with pytest.raises(ValueError, match="Invalid job evidence"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("research-assistant",),
        )


def test_build_release_manifest_rejects_duplicate_job_evidence_for_same_agent(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "artifacts"
    shared = dict(
        agent_name="research-assistant",
        agent_kind="prompt",
        environment="dev",
        run_id="42",
        run_attempt=1,
        job_status="succeeded",
        image_build_outcome="not_applicable",
        deploy_outcome="succeeded",
        image_reference=None,
        image_digest=None,
        deployment_record="deployments/research.json",
    )
    write_job_evidence(evidence_root / "job" / "a.json", JobEvidence(**shared))
    write_job_evidence(evidence_root / "job" / "b.json", JobEvidence(**shared))
    _write_deployment_record(
        evidence_root / "deployments" / "research.json",
        deployment_id="dupe-job",
        agent_name="research-assistant",
    )

    with pytest.raises(ValueError, match="Duplicate job evidence"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("research-assistant",),
        )


def test_build_release_manifest_rejects_mismatched_deployment_record_identity(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "research.json",
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="deployments/research.json",
        ),
    )
    _write_deployment_record(
        evidence_root / "deployments" / "research.json",
        deployment_id="wrong-agent",
        agent_name="architecture-advisor",
    )

    with pytest.raises(ValueError, match="does not match job evidence agent_name"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("research-assistant",),
        )


def test_build_release_manifest_requires_declared_deployment_record_match(tmp_path: Path) -> None:
    evidence_root = tmp_path / "artifacts"
    write_job_evidence(
        evidence_root / "job" / "research.json",
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record="deployments/expected.json",
        ),
    )
    _write_deployment_record(
        evidence_root / "deployments" / "other.json",
        deployment_id="other-record",
        agent_name="research-assistant",
    )

    manifest = build_release_manifest(
        _config(),
        evidence_root,
        _default_metadata(),
        selected_agents=("research-assistant",),
    )

    assert manifest["status"] == "incomplete"
    agent = manifest["agents"][0]
    assert agent["deployment_record"] is None


def _job_evidence_with_record(evidence_root: Path, reference: str) -> None:
    write_job_evidence(
        evidence_root / "artifact" / "evidence" / "research.json",
        JobEvidence(
            agent_name="research-assistant",
            agent_kind="prompt",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="not_applicable",
            deploy_outcome="succeeded",
            image_reference=None,
            image_digest=None,
            deployment_record=reference,
        ),
    )


@pytest.mark.parametrize(
    "reference",
    ["../../outside/research.json", "deployments/../../outside/research.json"],
)
def test_build_release_manifest_rejects_record_outside_its_artifact(
    tmp_path: Path, reference: str
) -> None:
    evidence_root = tmp_path / "artifacts"
    _job_evidence_with_record(evidence_root, reference)
    _write_deployment_record(
        evidence_root / "outside" / "research.json",
        deployment_id="foreign",
        agent_name="research-assistant",
    )

    with pytest.raises(ValueError, match="resolves outside its artifact directory"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("research-assistant",),
        )


def test_build_release_manifest_rejects_declared_record_that_is_not_a_record(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "artifacts"
    _job_evidence_with_record(evidence_root, "deployments/research.json")
    _write_json(
        evidence_root / "artifact" / "deployments" / "research.json",
        {"schema_version": "4", "agent_name": "research-assistant"},
    )

    with pytest.raises(ValueError, match="is not a valid deployment record file"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("research-assistant",),
        )


@pytest.mark.parametrize("job_status", ["succeeded", "failed"])
def test_build_release_manifest_rejects_successful_hosted_record_missing_image_fields(
    tmp_path: Path, job_status: str
) -> None:
    evidence_root = tmp_path / "artifacts"
    digest = "sha256:" + "c" * 64
    image_reference = _immutable_image_reference(
        "example.azurecr.io/architecture-advisor", digest
    )
    write_job_evidence(
        evidence_root / "job" / "hosted.json",
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status=job_status,
            image_build_outcome="succeeded",
            deploy_outcome="succeeded",
            image_reference=image_reference,
            image_digest=digest,
            deployment_record="deployments/architecture.json",
        ),
    )
    _write_json(
        evidence_root / "deployments" / "architecture.json",
        {
            "schema_version": "4",
            "deployment_id": "missing-images",
            "agent_name": "architecture-advisor",
            "status": "succeeded",
            "phase": "cutover_complete",
            "started_at": "2026-09-09T16:00:00+00:00",
            "completed_at": "2026-09-09T16:05:00+00:00",
        },
    )

    with pytest.raises(ValueError, match="must define non-empty image_reference and image_digest"):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("architecture-advisor",),
        )


@pytest.mark.parametrize(
    ("record_image_reference", "record_image_digest"),
    [
        (None, "sha256:" + "d" * 64),
        ("example.azurecr.io/architecture-advisor@sha256:" + "d" * 64, None),
        ("example.azurecr.io/architecture-advisor@sha256:" + "d" * 64, "sha256:" + "e" * 64),
    ],
)
def test_build_release_manifest_rejects_successful_hosted_record_null_or_mismatched_images(
    tmp_path: Path,
    record_image_reference: str | None,
    record_image_digest: str | None,
) -> None:
    evidence_root = tmp_path / "artifacts"
    digest = "sha256:" + "d" * 64
    image_reference = _immutable_image_reference(
        "example.azurecr.io/architecture-advisor", digest
    )
    write_job_evidence(
        evidence_root / "job" / "hosted.json",
        JobEvidence(
            agent_name="architecture-advisor",
            agent_kind="hosted",
            environment="dev",
            run_id="42",
            run_attempt=1,
            job_status="succeeded",
            image_build_outcome="succeeded",
            deploy_outcome="succeeded",
            image_reference=image_reference,
            image_digest=digest,
            deployment_record="deployments/architecture.json",
        ),
    )
    _write_json(
        evidence_root / "deployments" / "architecture.json",
        {
            "schema_version": "4",
            "deployment_id": "bad-images",
            "agent_name": "architecture-advisor",
            "status": "succeeded",
            "phase": "cutover_complete",
            "image_reference": record_image_reference,
            "image_digest": record_image_digest,
            "started_at": "2026-09-09T16:00:00+00:00",
            "completed_at": "2026-09-09T16:05:00+00:00",
        },
    )

    with pytest.raises(
        ValueError,
        match=(
            "must define non-empty image_reference and image_digest|"
            "does not match job evidence image_"
        ),
    ):
        build_release_manifest(
            _config(),
            evidence_root,
            _default_metadata(),
            selected_agents=("architecture-advisor",),
        )


def test_workflow_resolves_unique_hosted_image_tags(tmp_path: Path) -> None:
    github_output = tmp_path / "github-output"

    _run_workflow_step(
        "_deploy-agent.yml",
        "Resolve hosted image identity",
        tmp_path,
        {
            "ACR_LOGIN_SERVER": "example.azurecr.io",
            "IMAGE_REPOSITORY": "architecture-advisor",
            "SOURCE_SHA": "a" * 40,
            "RUN_ID": "42",
            "RUN_ATTEMPT": "2",
            "GITHUB_OUTPUT": str(github_output),
        },
    )

    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
    )
    repository = "example.azurecr.io/architecture-advisor"
    assert outputs["repository"] == repository
    assert outputs["tagged_reference"] == f"{repository}:run-42-attempt-2"
    assert outputs["tags"] == (
        f"{repository}:run-42-attempt-2,{repository}:sha-{'a' * 40}"
    )
    # No tag may resolve to latest; the composite action rejects it as well.
    assert "latest" not in outputs["tags"]


def test_workflow_resolve_fails_without_registry_login_server(tmp_path: Path) -> None:
    result = _run_workflow_step(
        "_deploy-agent.yml",
        "Resolve hosted image identity",
        tmp_path,
        {
            "ACR_LOGIN_SERVER": "",
            "IMAGE_REPOSITORY": "architecture-advisor",
            "SOURCE_SHA": "a" * 40,
            "RUN_ID": "42",
            "RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "ACR_LOGIN_SERVER" in result.stdout


@pytest.mark.parametrize(
    "digest",
    ["", "sha256:not-hex", "sha256:" + "A" * 64, "a" * 64],
)
def test_workflow_export_rejects_invalid_hosted_image_digest(
    tmp_path: Path, digest: str
) -> None:
    github_env = tmp_path / "github-env"

    result = _run_workflow_step(
        "_deploy-agent.yml",
        "Export hosted image identity",
        tmp_path,
        {
            "IMAGE_TAGGED_REFERENCE": "example.azurecr.io/a:run-1-attempt-1",
            "IMAGE_REPOSITORY_REFERENCE": "example.azurecr.io/a",
            "IMAGE_DIGEST": digest,
            "IMAGE_REFERENCE_ENV_NAME": ARCHITECTURE_ADVISOR_SPEC.image_env_var,
            "IMAGE_DIGEST_ENV_NAME": ARCHITECTURE_ADVISOR_SPEC.image_digest_env_var,
            "GITHUB_ENV": str(github_env),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "invalid digest" in result.stdout
    assert not github_env.exists()


def test_workflow_export_rejects_invalid_image_environment_variable_name(
    tmp_path: Path,
) -> None:
    github_env = tmp_path / "github-env"

    result = _run_workflow_step(
        "_deploy-agent.yml",
        "Export hosted image identity",
        tmp_path,
        {
            "IMAGE_TAGGED_REFERENCE": "example.azurecr.io/a:run-1-attempt-1",
            "IMAGE_REPOSITORY_REFERENCE": "example.azurecr.io/a",
            "IMAGE_DIGEST": "sha256:" + "a" * 64,
            "IMAGE_REFERENCE_ENV_NAME": "PATH; rm -rf /",
            "IMAGE_DIGEST_ENV_NAME": ARCHITECTURE_ADVISOR_SPEC.image_digest_env_var,
            "GITHUB_ENV": str(github_env),
        },
        check=False,
    )

    assert result.returncode != 0
    assert "Invalid image environment variable name" in result.stdout
