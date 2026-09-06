FROM python:3.11-slim-bookworm
WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --disable-pip-version-check --no-cache-dir --retries 5 --timeout 120 uv==0.6.5
ENV UV_HTTP_TIMEOUT=120 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.11
ENV PATH="/app/.venv/bin:$PATH"
COPY app ./app
COPY webui ./webui
COPY resource ./resource
COPY config.example.toml ./config.example.toml
RUN useradd --create-home studio && mkdir -p /app/storage && chown studio:studio /app /app/storage
USER studio
EXPOSE 8501
CMD ["streamlit", "run", "webui/Main.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false", "--server.enableXsrfProtection=true", "--server.enableCORS=true", "--server.maxUploadSize=90", "--client.toolbarMode=minimal"]
