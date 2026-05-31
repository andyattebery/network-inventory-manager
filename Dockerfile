FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg && \
    curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg && \
    ARCH="$(dpkg --print-architecture)" && \
    echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/${ARCH} stable main" \
    > /etc/apt/sources.list.d/1password.list && \
    apt-get update && apt-get install -y --no-install-recommends 1password-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
EXPOSE 8080
VOLUME [ "/config" ]
ENTRYPOINT ["python", "-m", "network_inventory_manager"]
CMD ["--interval", "1800"]
