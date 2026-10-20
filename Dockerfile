# The virtualenv is built with uv from uv.lock in a builder stage, and only the venv is copied into
# the runtime stage: uv's Docker guide (https://docs.astral.sh/uv/guides/integration/docker/),
# "Non-editable installs", "Caching" and "Compiling bytecode". The dependency layer — uv.lock and
# pyproject.toml bind-mounted, `--locked` — is the form in Astral's uv-docker-example
# (multistage.Dockerfile); the guide's own example mounts only uv.lock with `--frozen`. Copying files
# by name rather than `COPY . /app` is this repo's choice: config.yaml with live secrets sits in the
# build context.
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

# UV_PYTHON_DOWNLOADS=0 keeps uv on this image's interpreter: the venv links to it, and the runtime
# stage below has the same one at the same path.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable
COPY pyproject.toml uv.lock ./
COPY network_inventory_manager ./network_inventory_manager
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM python:3.12-slim

# The 1Password CLI is pinned in op-cli.toml, which nix/op-cli.nix reads too, so
# this image and the nix package ship the same `op`. Bump it there, not here —
# resolving secrets is the one thing this image cannot do wrong, so an `op` change
# should only ever land on a deliberate bump. The download is checked against the
# pinned sha256, and running `op --version` fails the build unless the binary
# executes here and reports the pinned version.
COPY op-cli.toml /tmp/op-cli.toml
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    ARCH="$(dpkg --print-architecture)" && \
    OP_VERSION="$(python -c 'import tomllib; print(tomllib.load(open("/tmp/op-cli.toml", "rb"))["version"])')" && \
    OP_SHA256="$(python -c 'import sys, tomllib; print(tomllib.load(open("/tmp/op-cli.toml", "rb"))["sha256"]["linux_" + sys.argv[1]])' "$ARCH")" && \
    curl -fsSL -o /tmp/op.zip "https://cache.agilebits.com/dist/1P/op2/pkg/v${OP_VERSION}/op_linux_${ARCH}_v${OP_VERSION}.zip" && \
    echo "${OP_SHA256}  /tmp/op.zip" | sha256sum -c - && \
    python -m zipfile -e /tmp/op.zip /tmp/op && \
    install -m 0755 /tmp/op/op /usr/local/bin/op && \
    rm -rf /tmp/op /tmp/op.zip /tmp/op-cli.toml && \
    test "$(op --version)" = "$OP_VERSION"
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
VOLUME [ "/config" ]
ENTRYPOINT ["python", "-m", "network_inventory_manager"]
CMD ["--interval", "1800"]
