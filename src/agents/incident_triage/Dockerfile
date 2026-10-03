FROM ghcr.io/astral-sh/uv@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv
FROM python@sha256:e06cc1111ed84189e91866447f562b89faadbfbbb9937cd67e6bf4172cdb45df

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING="true"
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --compile-bytecode
COPY main.py flock_app.py model_endpoint.py ./
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent
EXPOSE 8088
CMD ["python", "main.py"]
