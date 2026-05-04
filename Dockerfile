FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

# Base runtime dependencies. Node.js is only installed in the full target below.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git openssh-client && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Enable uv bytecode compilation for faster startup
ENV UV_COMPILE_BYTECODE=1
# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install core Python dependencies first (Leverage Docker cache + uv cache).
# Optional gateway/full extras are installed only in derived targets.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy only the source needed for the default Python package install.
COPY pyproject.toml uv.lock README.md LICENSE /app/
COPY src /app/src
COPY skills /app/skills

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Put the virtual environment in the PATH
ENV PATH="/app/.venv/bin:$PATH"

# Create non-root user (UID 1000 to match typical host user for bind mounts)
RUN groupadd -g 1000 theos && useradd -u 1000 -g theos -m theos \
    && mkdir -p /home/theos/.theos \
    && chown -R theos:theos /home/theos/.theos

FROM base AS core

USER theos

ENTRYPOINT ["theos"]
CMD ["agent"]

FROM base AS gateway

# Gateway scheduling support without pulling UI/channel/full extras.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra gateway

USER theos

EXPOSE 18790

ENTRYPOINT ["theos"]
CMD ["gateway"]

FROM base AS full

# Full-only assets: dashboard UI, WhatsApp bridge, and instinct scripts/domain data.
COPY ui /app/ui
COPY bridge /app/bridge
COPY instinct /app/instinct

# Optional full image: install every Python extra and build UI + WhatsApp bridge.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --all-extras

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/ui
RUN npm install && npm run build

WORKDIR /app/bridge
RUN git config --global url."https://github.com/".insteadOf ssh://git@github.com/ && npm install && npm run build
WORKDIR /app

USER theos

EXPOSE 18790

ENTRYPOINT ["theos"]
CMD ["gateway"]

FROM gateway AS runtime
