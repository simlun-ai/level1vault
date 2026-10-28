# syntax=docker/dockerfile:1
ARG DEBIAN_IMAGE=debian:trixie

# Install only the tools required by source checks.
FROM ${DEBIAN_IMAGE} AS tools
WORKDIR /src
RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN --mount=type=cache,id=level1vault-ci-apt-packages,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=level1vault-ci-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    bash ca-certificates git make python3 shellcheck dpkg-dev

# Copy allowlisted sources without host artifacts or Git configuration.
FROM tools AS source
COPY --link . .
ENV PYTHONDONTWRITEBYTECODE=1

# Exercise checkout-dependent wrappers against a disposable source repository.
FROM source AS unit-source
RUN --network=none git init -q -b main && git add . && \
    git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm 'CI source fixture'

FROM source AS shell
RUN --network=none bash pipeline/lint.sh --source-tree

FROM unit-source AS unit-pipeline
RUN --network=none python3 pipeline/test.py pipeline

FROM unit-source AS unit-runtime
RUN --network=none python3 pipeline/test.py runtime

FROM unit-source AS unit-stages
RUN --network=none python3 pipeline/test.py stages

# Keep the pinned linter separate from operational and unit-test environments.
FROM python:3.14-slim AS lint-tools
RUN pip install --no-cache-dir ruff==0.17.2
WORKDIR /src

FROM lint-tools AS ruff
COPY --link . .
RUN --network=none ruff check --no-cache pipeline stages runtime common integration-tests && \
    ruff format --check --no-cache pipeline stages runtime common integration-tests
