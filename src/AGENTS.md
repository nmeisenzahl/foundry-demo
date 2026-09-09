# Source Guide

- Keep agent definitions declarative and export each package's `AGENT_SPEC`.
- Put reusable agent contracts and validators in `agents/common/`.
- Keep Foundry SDK adapters in `delivery/`; agent packages must not own release routing.
- Preserve immutable version creation, exact-version smoke tests, and promote-after-success behavior.
- Add or update focused tests in `tests/` for behavior changes.
