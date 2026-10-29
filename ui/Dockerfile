# The demo UI (app.py), plus the small helpers the compose file and scripts run from this image:
# init_storage.py (creates the lake container) and resnapshot.py (queues ad-hoc snapshots).
FROM python:3.13-slim-trixie@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The UI's user store (auth.db) lives on the ui-data volume mounted here; a new named volume
# takes this directory's owner, the UI's user.
RUN mkdir /data && chown 65532:65532 /data
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
