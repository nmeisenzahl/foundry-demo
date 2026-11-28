FROM ghcr.io/astral-sh/uv@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv
FROM python@sha256:1eb6b7d4b76454b1de8317863ac3213b678c337b27e604a4e3fb70bddbb2bad7

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    FLOCK_AUTO_TRACE="false"
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --compile-bytecode
COPY main.py flock_app.py ./
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent
EXPOSE 8088
CMD ["python", "main.py"]
