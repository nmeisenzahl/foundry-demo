import copy
import json
from pathlib import Path

import pytest

from foundry_demo.agents import HostedAgentSpec, get_agent, list_agents
from foundry_demo.delivery.automation import (
    AutomationConfigError,
    load_automation_config,
    main,
    render_matrix,
    select_agents,
    select_changed_agents,
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _config_path() -> Path:
    return _repository_root() / ".github/deployment/config.json"


def _load_raw_config() -> dict:
    return json.loads(_config_path().read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_load_automation_config_matches_reviewed_registry() -> None:
    repository_root = _repository_root()
    config = load_automation_config(repository_root / ".github/deployment/config.json")

    assert config.schema_version == "2"
    assert tuple(config.environments) == ("dev",)
    assert config.environments["dev"].github_environment == "dev"
    assert isinstance(config.environments["dev"].artifact_retention_days, int)
    assert config.environments["dev"].artifact_retention_days > 0

    assert {agent.name: agent.kind for agent in config.agents} == {
        name: get_agent(name).kind.value for name in list_agents()
    }

    for agent in config.agents:
        spec = get_agent(agent.name)
        if isinstance(spec, HostedAgentSpec):
            assert agent.image_reference_env == spec.image_env_var
            assert agent.image_digest_env == spec.image_digest_env_var
        else:
            assert agent.image_context == ""
            assert agent.image_repository == ""
            assert agent.image_reference_env == ""
            assert agent.image_digest_env == ""


def test_select_agents_supports_all_single_and_comma_separated() -> None:
    config = load_automation_config(_config_path())

    assert tuple(agent.name for agent in select_agents(config, "all")) == tuple(list_agents())
    assert tuple(agent.name for agent in select_agents(config, "research-assistant")) == (
        "research-assistant",
    )
    assert tuple(
        agent.name
        for agent in select_agents(
            config,
            "research-assistant, architecture-advisor, incident-triage, "
            "release-notes-writer",
        )
    ) == tuple(list_agents())


@pytest.mark.parametrize(
    ("selector", "expected_match"),
    [
        ("", "cannot be empty"),
        ("  ", "cannot be empty"),
        (",", "blank"),
        ("architecture-advisor,,research-assistant", "blank"),
        ("architecture-advisor,architecture-advisor", "duplicate"),
        ("architecture-advisor,not-a-real-agent", "Unknown"),
    ],
)
def test_select_agents_rejects_blank_duplicate_and_unknown_names(
    selector: str, expected_match: str
) -> None:
    config = load_automation_config(_config_path())
    with pytest.raises(AutomationConfigError, match=expected_match):
        select_agents(config, selector)


def test_load_automation_config_rejects_invalid_schema_and_data(tmp_path: Path) -> None:
    data = _load_raw_config()

    wrong_schema = copy.deepcopy(data)
    wrong_schema["schema_version"] = "999"
    wrong_schema_path = tmp_path / "wrong_schema.json"
    _write_json(wrong_schema_path, wrong_schema)
    with pytest.raises(AutomationConfigError, match="schema_version"):
        load_automation_config(wrong_schema_path)

    missing_environments = copy.deepcopy(data)
    missing_environments["environments"] = {}
    missing_environments_path = tmp_path / "missing_environments.json"
    _write_json(missing_environments_path, missing_environments)
    with pytest.raises(AutomationConfigError, match="environments"):
        load_automation_config(missing_environments_path)

    duplicate_names = copy.deepcopy(data)
    duplicate_names["agents"] = [duplicate_names["agents"][0], duplicate_names["agents"][0]]
    duplicate_names_path = tmp_path / "duplicate_names.json"
    _write_json(duplicate_names_path, duplicate_names)
    with pytest.raises(AutomationConfigError, match="Duplicate"):
        load_automation_config(duplicate_names_path)

    invalid_kind = copy.deepcopy(data)
    invalid_kind["agents"][0]["kind"] = "other"
    invalid_kind_path = tmp_path / "invalid_kind.json"
    _write_json(invalid_kind_path, invalid_kind)
    with pytest.raises(AutomationConfigError, match="kind"):
        load_automation_config(invalid_kind_path)

    non_positive_retention = copy.deepcopy(data)
    non_positive_retention["environments"]["dev"]["artifact_retention_days"] = 0
    non_positive_retention_path = tmp_path / "non_positive_retention.json"
    _write_json(non_positive_retention_path, non_positive_retention)
    with pytest.raises(AutomationConfigError, match="artifact_retention_days"):
        load_automation_config(non_positive_retention_path)

    incomplete_hosted = copy.deepcopy(data)
    hosted_agent = next(
        agent for agent in incomplete_hosted["agents"] if agent["kind"] == "hosted"
    )
    hosted_agent["image_reference_env"] = ""
    incomplete_hosted_path = tmp_path / "incomplete_hosted.json"
    _write_json(incomplete_hosted_path, incomplete_hosted)
    with pytest.raises(AutomationConfigError, match="hosted"):
        load_automation_config(incomplete_hosted_path)

    prompt_has_image_metadata = copy.deepcopy(data)
    prompt_agent = next(
        agent for agent in prompt_has_image_metadata["agents"] if agent["kind"] == "prompt"
    )
    prompt_agent["image_repository"] = "unexpected"
    prompt_has_image_metadata_path = tmp_path / "prompt_has_image_metadata.json"
    _write_json(prompt_has_image_metadata_path, prompt_has_image_metadata)
    with pytest.raises(AutomationConfigError, match="prompt"):
        load_automation_config(prompt_has_image_metadata_path)


def test_render_matrix_returns_compact_registry_sorted_json() -> None:
    config = load_automation_config(_config_path())

    matrix = render_matrix(config, "all", "dev")

    payload = json.loads(matrix)
    assert matrix == json.dumps(payload, separators=(",", ":"))
    assert list(payload) == ["include"]
    assert payload["include"] == [
        {
            "agent_name": "architecture-advisor",
            "agent_kind": "hosted",
            "environment": "dev",
            "github_environment": "dev",
            "artifact_retention_days": 90,
            "image_context": "src/agents/architecture_advisor",
            "image_repository": "architecture-advisor",
            "image_reference_env": "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE",
            "image_digest_env": "FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE_DIGEST",
        },
        {
            "agent_name": "incident-triage",
            "agent_kind": "hosted",
            "environment": "dev",
            "github_environment": "dev",
            "artifact_retention_days": 90,
            "image_context": "src/agents/incident_triage",
            "image_repository": "incident-triage",
            "image_reference_env": "FOUNDRY_INCIDENT_TRIAGE_IMAGE",
            "image_digest_env": "FOUNDRY_INCIDENT_TRIAGE_IMAGE_DIGEST",
        },
        {
            "agent_name": "release-notes-writer",
            "agent_kind": "prompt",
            "environment": "dev",
            "github_environment": "dev",
            "artifact_retention_days": 90,
            "image_context": "",
            "image_repository": "",
            "image_reference_env": "",
            "image_digest_env": "",
        },
        {
            "agent_name": "research-assistant",
            "agent_kind": "prompt",
            "environment": "dev",
            "github_environment": "dev",
            "artifact_retention_days": 90,
            "image_context": "",
            "image_repository": "",
            "image_reference_env": "",
            "image_digest_env": "",
        },
    ]


def test_render_matrix_rejects_unknown_environment() -> None:
    config = load_automation_config(_config_path())
    with pytest.raises(AutomationConfigError, match="Unknown environment"):
        render_matrix(config, "all", "staging")


def test_load_automation_config_exposes_change_detection_paths() -> None:
    config = load_automation_config(_config_path())

    assert config.shared_paths
    assert all(path and not path.startswith("/") for path in config.shared_paths)

    for agent in config.agents:
        assert agent.source_paths
        assert all(path and not path.startswith("/") for path in agent.source_paths)
        if agent.image_context:
            assert any(
                agent.image_context.startswith(path.rstrip("/"))
                for path in agent.source_paths
            )


def test_select_changed_agents_selects_only_affected_agents() -> None:
    config = load_automation_config(_config_path())

    selected = select_changed_agents(
        config, ["src/agents/architecture_advisor/tools.py"]
    )

    assert tuple(agent.name for agent in selected) == ("architecture-advisor",)


def test_select_changed_agents_returns_every_agent_for_shared_paths() -> None:
    config = load_automation_config(_config_path())

    selected = select_changed_agents(config, ["src/delivery/release.py"])

    assert tuple(agent.name for agent in selected) == tuple(list_agents())


def test_select_changed_agents_returns_nothing_for_unrelated_paths() -> None:
    config = load_automation_config(_config_path())

    assert select_changed_agents(config, ["README.md", "docs/Setup.md"]) == ()
    assert select_changed_agents(config, []) == ()


def test_select_changed_agents_normalizes_and_ignores_blank_entries() -> None:
    config = load_automation_config(_config_path())

    selected = select_changed_agents(
        config, ["", "  ", "./src/agents/architecture_advisor/main.py"]
    )

    assert tuple(agent.name for agent in selected) == ("architecture-advisor",)


def test_select_changed_agents_matches_exact_file_paths_without_prefix_bleed() -> None:
    config = load_automation_config(_config_path())

    assert select_changed_agents(config, ["pyproject.toml.bak"]) == ()
    assert select_changed_agents(config, ["pyproject.toml"]) != ()


def test_render_matrix_filters_include_by_changed_paths() -> None:
    config = load_automation_config(_config_path())

    matrix = json.loads(
        render_matrix(
            config,
            "all",
            "dev",
            changed_paths=["src/agents/architecture_advisor/Dockerfile"],
        )
    )
    assert [entry["agent_name"] for entry in matrix["include"]] == [
        "architecture-advisor"
    ]

    unrelated = json.loads(
        render_matrix(config, "all", "dev", changed_paths=["README.md"])
    )
    assert unrelated["include"] == []


def test_render_matrix_intersects_explicit_selector_with_changed_paths() -> None:
    config = load_automation_config(_config_path())

    matrix = json.loads(
        render_matrix(
            config,
            "research-assistant",
            "dev",
            changed_paths=["src/agents/architecture_advisor/tools.py"],
        )
    )

    assert matrix["include"] == []


@pytest.mark.parametrize(
    ("source_paths", "expected_match"),
    [
        ([], "at least one"),
        ("src/agents/research_assistant", "must be a JSON array"),
        ([""], "cannot be blank"),
        (["   "], "cannot be blank"),
        (["/src/agents/research_assistant"], "must be repository-relative"),
        (["src/../etc/passwd"], "must be repository-relative"),
        (["src\\agents"], "must be repository-relative"),
        (["src/agents/research_assistant", "src/agents/research_assistant"], "Duplicate"),
        ([5], "must be a string"),
    ],
)
def test_load_automation_config_rejects_invalid_source_paths(
    tmp_path: Path, source_paths: object, expected_match: str
) -> None:
    data = _load_raw_config()
    data["agents"][1]["source_paths"] = source_paths
    config_path = tmp_path / "invalid_source_paths.json"
    _write_json(config_path, data)

    with pytest.raises(AutomationConfigError, match=expected_match):
        load_automation_config(config_path)


@pytest.mark.parametrize(
    ("shared_paths", "expected_match"),
    [
        ([], "at least one"),
        (["", "src/delivery/"], "cannot be blank"),
        (["/src/delivery/"], "must be repository-relative"),
        (["src/delivery/", "src/delivery/"], "Duplicate"),
    ],
)
def test_load_automation_config_rejects_invalid_shared_paths(
    tmp_path: Path, shared_paths: object, expected_match: str
) -> None:
    data = _load_raw_config()
    data["shared_paths"] = shared_paths
    config_path = tmp_path / "invalid_shared_paths.json"
    _write_json(config_path, data)

    with pytest.raises(AutomationConfigError, match=expected_match):
        load_automation_config(config_path)


def test_main_renders_matrix_filtered_by_changed_paths_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changed_paths_file = tmp_path / "changed.txt"
    changed_paths_file.write_text(
        "docs/Setup.md\nsrc/agents/architecture_advisor/main.py\n", encoding="utf-8"
    )

    main(
        [
            "--config",
            str(_config_path()),
            "--environment",
            "dev",
            "--agents",
            "all",
            "--changed-paths-file",
            str(changed_paths_file),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert [entry["agent_name"] for entry in payload["include"]] == [
        "architecture-advisor"
    ]


def test_main_rejects_missing_changed_paths_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--config",
                str(_config_path()),
                "--environment",
                "dev",
                "--agents",
                "all",
                "--changed-paths-file",
                str(tmp_path / "absent.txt"),
            ]
        )

    assert excinfo.value.code == 2
