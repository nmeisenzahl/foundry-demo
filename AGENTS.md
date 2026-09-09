# Contributor Guide

- Treat `README.md` as the concise project entry point.
- Treat `docs/` as the canonical location for detailed setup, deployment,
  operations, and roadmap guidance.
- Use Python 3.11 for the root project and `uv` for dependency and command execution.
- Run `uv run ruff check .` and `uv run pytest -q` after Python changes.
- Keep generated files, local state, secrets, caches, and deployment artifacts untracked.
- Keep agent-specific code under `src/agents/` and delivery orchestration under `src/delivery/`.
