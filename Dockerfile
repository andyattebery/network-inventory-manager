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
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
EXPOSE 8080
VOLUME [ "/config" ]
ENTRYPOINT ["python", "-m", "network_inventory_manager"]
CMD ["--interval", "1800"]
