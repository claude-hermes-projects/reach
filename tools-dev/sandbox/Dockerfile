# The sealed workspace where models write code and where that code is tested.
# It holds a throwaway copy of the repository and nothing else: no GitHub key, no files of the host.
# Build (the runner's "doctor" says when):  docker build --build-arg AIDER_VERSION=<x.y.z> -t agent-sandbox tools-dev/sandbox
FROM python:3.12-slim
ARG AIDER_VERSION
RUN test -n "$AIDER_VERSION" || (echo "Set AIDER_VERSION to an exact version" && exit 1)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "aider-chat==${AIDER_VERSION}"
LABEL agent.aider.version="${AIDER_VERSION}"
RUN useradd --create-home coder && git config --system --add safe.directory /work
USER coder
WORKDIR /work
