FROM ghcr.io/astral-sh/uv@sha256:a19035b77e3ba952fe48e88ff93d160853d8b2abeccc1a19571573fdb1fa78aa AS uv
FROM python@sha256:e06cc1111ed84189e91866447f562b89faadbfbbb9937cd67e6bf4172cdb45df

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING="true"
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --compile-bytecode
COPY main.py toolbox_endpoint.py ./
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent
EXPOSE 8088
CMD ["python", "main.py"]
