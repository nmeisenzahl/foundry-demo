import pytest

from foundry_demo.delivery.locking import ConcurrentDeploymentError, deployment_lock


def test_deployment_lock_rejects_concurrent_writer() -> None:
    endpoint = "https://example.services.ai.azure.com/api/projects/example"

    with (
        deployment_lock(project_endpoint=endpoint, agent_name="research-assistant"),
        pytest.raises(ConcurrentDeploymentError, match="already running"),
        deployment_lock(
            project_endpoint=endpoint,
            agent_name="research-assistant",
        ),
    ):
        pytest.fail("Concurrent deployment lock should not be acquired.")


def test_deployment_lock_allows_different_agents() -> None:
    endpoint = "https://example.services.ai.azure.com/api/projects/example"

    with (
        deployment_lock(project_endpoint=endpoint, agent_name="agent-one"),
        deployment_lock(project_endpoint=endpoint, agent_name="agent-two"),
    ):
        pass


def test_deployment_lock_canonicalizes_trailing_slash() -> None:
    endpoint = "https://example.services.ai.azure.com/api/projects/example"

    with (
        deployment_lock(project_endpoint=endpoint, agent_name="research-assistant"),
        pytest.raises(ConcurrentDeploymentError, match="already running"),
        deployment_lock(
            project_endpoint=f"{endpoint}/",
            agent_name="research-assistant",
        ),
    ):
        pytest.fail("Equivalent endpoints must share one deployment lock.")
