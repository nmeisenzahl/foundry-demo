"""Validation for immutable Foundry Toolbox MCP endpoints."""

from urllib.parse import parse_qs, unquote, urlparse


def validate_toolbox_endpoint(endpoint: str, *, project_endpoint: str) -> str:
    """Validate an HTTPS, version-pinned Toolbox endpoint in the selected project."""
    cleaned = endpoint.strip()
    parsed_endpoint = urlparse(cleaned)
    parsed_project = urlparse(project_endpoint.strip().rstrip("/"))

    if parsed_endpoint.scheme != "https":
        raise ValueError(f"Toolbox endpoint must use https scheme: {cleaned!r}")
    if parsed_endpoint.username or parsed_endpoint.password or parsed_endpoint.fragment:
        raise ValueError(f"Toolbox endpoint contains unsupported URL components: {cleaned!r}")
    if parsed_endpoint.netloc.lower() != parsed_project.netloc.lower():
        raise ValueError(
            f"Toolbox endpoint host {parsed_endpoint.netloc!r} does not match "
            f"project host {parsed_project.netloc!r}."
        )

    project_path = parsed_project.path.rstrip("/")
    expected_prefix = f"{project_path}/toolboxes/"
    if not parsed_endpoint.path.startswith(expected_prefix):
        raise ValueError(
            f"Toolbox endpoint path {parsed_endpoint.path!r} is outside "
            f"project path {project_path!r}."
        )

    suffix = parsed_endpoint.path[len(expected_prefix) :]
    segments = suffix.split("/")
    if (
        len(segments) != 4
        or not segments[0]
        or segments[1] != "versions"
        or not segments[2]
        or segments[3] != "mcp"
    ):
        raise ValueError(
            "Toolbox endpoint must match "
            f"<project>/toolboxes/<name>/versions/<version>/mcp: {cleaned!r}"
        )
    for segment in (segments[0], segments[2]):
        if "/" in unquote(segment):
            raise ValueError(f"Toolbox endpoint contains an encoded path separator: {cleaned!r}")

    query = parse_qs(parsed_endpoint.query, keep_blank_values=True, strict_parsing=True)
    if query and query != {"api-version": ["v1"]}:
        raise ValueError(f"Toolbox endpoint has unsupported query parameters: {cleaned!r}")

    return cleaned
