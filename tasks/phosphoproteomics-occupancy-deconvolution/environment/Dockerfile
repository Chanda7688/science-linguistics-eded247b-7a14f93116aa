# AGENT container image. Bake the environment the agent works in. Do NOT copy solution/ or tests/.
FROM python:3.13-slim-bookworm@sha256:REPLACE-with-pinned-digest

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends     ca-certificates  && rm -rf /var/lib/apt/lists/*

# COPY app/ /app/        # optional: seed the working environment (buggy code, data, etc.)
# COPY data /root/data   # optional: input data files (never solution/ground-truth)
