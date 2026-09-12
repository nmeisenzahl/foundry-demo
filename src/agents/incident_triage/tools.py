"""Smoke acceptance validation for the incident-triage agent."""

from foundry_demo.agents.common.validators import validate_output_items
from foundry_demo.delivery.smoke import SmokeEvidence, SmokeTestError

# One message item per blackboard artifact: ImpactAssessment, RootCauseHypothesis,
# and the ActionPlan that joins them.
EXPECTED_ARTIFACT_COUNT = 3


def validate_smoke(evidence: SmokeEvidence) -> None:
    """Validate that the full blackboard cascade ran.

    The hosted runtime emits exactly one output item per published artifact, so
    three message items can only exist if both parallel agents *and* the joining
    commander published. A partial cascade produces fewer items and fails here.
    """
    failures: list[str] = []

    err_items = validate_output_items(
        evidence, "message", min_count=EXPECTED_ARTIFACT_COUNT
    )
    if err_items:
        failures.append(err_items)

    if failures:
        raise SmokeTestError("; ".join(failures), evidence=evidence)
