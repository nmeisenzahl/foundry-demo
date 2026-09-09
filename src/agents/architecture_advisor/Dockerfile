FROM ghcr.io/astral-sh/uv@sha256:a19035b77e3ba952fe48e88ff93d160853d8b2abeccc1a19571573fdb1fa78aa AS uv
FROM python@sha256:a7cb177dc60dfa77b223192cd0d9e6ce1aac7cf9a569023d68e7c91b6371a718

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
