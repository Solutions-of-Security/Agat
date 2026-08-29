FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 agat && mkdir -p /state && chown agat:agat /state
COPY workers/requirements.txt /opt/agat/requirements.txt
RUN pip install --no-cache-dir --requirement /opt/agat/requirements.txt
COPY --chown=agat:agat workers/agat_worker.py /opt/agat/agat_worker.py
COPY --chown=agat:agat workers/web_tools.py /opt/agat/web_tools.py
COPY --chown=agat:agat workers/telemetry.py /opt/agat/telemetry.py

USER agat
ENTRYPOINT ["python", "/opt/agat/agat_worker.py"]
