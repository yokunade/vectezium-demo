# The demo UI (app.py), plus the small helpers the compose file and scripts run from this image:
# init_storage.py (creates the lake container), send_signal.py (sends Vectezium signals) and
# resnapshot.py (queues ad-hoc snapshots in the offset file while Vectezium is stopped).
FROM python:3.13-slim-trixie@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The UI's user store (auth.db) lives on the ui-data volume mounted at /data, and Vectezium's
# signal folders on the signals volume at /signals; a new named volume takes its directory's
# owner, the UI's user. 65532 is also Vectezium's user, which owns the live
# files the UI trims (the compose file runs the UI as the same user).
RUN mkdir /data /signals && chown 65532:65532 /data /signals
USER 65532:65532
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
