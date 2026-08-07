FROM python:3.12-slim

# Pinned so an `op` change lands on a deliberate bump rather than silently on the
# next image build — resolving secrets is the one thing this image cannot do
# wrong. Note 1Password's apt repo keeps only the current release, so when they
# ship a new one this build fails with "Version '...' not found". That failure is
# the point: bump this, don't unpin it.
ARG OP_CLI_VERSION=2.38.1-1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg && \
    curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg && \
    ARCH="$(dpkg --print-architecture)" && \
    echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/${ARCH} stable main" \
    > /etc/apt/sources.list.d/1password.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends "1password-cli=${OP_CLI_VERSION}" && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
EXPOSE 8080
VOLUME [ "/config" ]
ENTRYPOINT ["python", "-m", "network_inventory_manager"]
CMD ["--interval", "1800"]
