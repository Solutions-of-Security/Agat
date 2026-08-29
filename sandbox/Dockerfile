FROM python:3.13-slim@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --gid 65532 sandbox && useradd --no-create-home --uid 65532 --gid 65532 sandbox
COPY sandbox/requirements.txt /opt/agat-sandbox/requirements.txt
RUN pip install --no-cache-dir --require-hashes --requirement /opt/agat-sandbox/requirements.txt
COPY --chown=65532:65532 sandbox/runner.py /opt/agat-sandbox/runner.py

USER 65532:65532
ENTRYPOINT ["python", "/opt/agat-sandbox/runner.py"]
