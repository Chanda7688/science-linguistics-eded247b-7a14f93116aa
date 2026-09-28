# VERIFIER container image (separate mode). Owns everything the verifier needs — test.sh must
# install/fetch NOTHING at verify time. Runs after the agent container is torn down.
FROM python:3.13-slim-bookworm@sha256:REPLACE-with-pinned-digest
ENV DEBIAN_FRONTEND=noninteractive

# Canonical CTRF toolchain — pins enforced repo-wide by check-pytest-version
RUN pip install --no-cache-dir pytest==9.1.1 pytest-json-ctrf==0.5.2

COPY . /tests/          # bake tests in — harbor does NOT upload tests/ at verify time
RUN mkdir -p /app        # pre-create the parent dir of every declared artifact (see task.toml artifacts=[])
