FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIAFORGE_ARTIFACT_ROOT=/var/lib/mediaforge/artifacts \
    MEDIAFORGE_STATE_BACKEND=sqlite \
    MEDIAFORGE_AUTH_MODE=required

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir '.[enterprise,agents,temporal]'

RUN mkdir -p /var/lib/mediaforge/artifacts
VOLUME ["/var/lib/mediaforge"]
EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8020/livez', timeout=4).close()"

CMD ["uvicorn", "mediaforge_p1.api:app", "--host", "0.0.0.0", "--port", "8020"]
