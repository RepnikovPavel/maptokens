FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl wget ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
ENV PYTHONUNBUFFERED=1 HF_HUB_DISABLE_PROGRESS_BARS=1 HF_HUB_DISABLE_TELEMETRY=1
ENTRYPOINT ["kitscenes"]
