FROM ghcr.io/astral-sh/uv@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv
FROM python@sha256:46837ed3105db0029b6d656816185c6258e3b39690532fb782428e0a5fd76182

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
