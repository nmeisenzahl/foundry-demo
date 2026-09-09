"""Cross-process locking for local Foundry agent deployments."""

import fcntl
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class ConcurrentDeploymentError(RuntimeError):
    """Raised when another local process is deploying the same agent."""


@contextmanager
def deployment_lock(*, project_endpoint: str, agent_name: str) -> Iterator[None]:
    """Fail closed when another process is deploying the same project agent."""
    canonical_endpoint = project_endpoint.strip().rstrip("/")
    target = f"{canonical_endpoint}\0{agent_name}".encode()
    lock_name = f"{hashlib.sha256(target).hexdigest()}.lock"
    lock_dir = Path(tempfile.gettempdir()) / "foundry-demo-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / lock_name

    with open(lock_path, "a+", encoding="utf-8") as handle:
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.seek(0)
                holder = handle.read().strip()
                holder_detail = f" (process {holder})" if holder else ""
                raise ConcurrentDeploymentError(
                    f"Another deployment for {agent_name!r} is already running{holder_detail}."
                ) from exc

            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
