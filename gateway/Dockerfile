FROM rust:1-alpine AS build-rust-mcp-filesystem
RUN mkdir /dist
RUN cargo install --locked rust-mcp-filesystem@0.4.1
RUN cp $(which rust-mcp-filesystem) /dist

FROM python:3.14-alpine AS build
# We need to build in the same workdir that the app stage will use because
# we'll copy our entire .venv over to it, which contains symlinks that need
# to continue working.
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/bin/
RUN apk add --no-cache build-base olm
# Install only dependencies first.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
# Install our actual project.
COPY src src
COPY LICENSE README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

FROM build AS test
COPY --from=build-rust-mcp-filesystem /dist/rust-mcp-filesystem /usr/local/bin/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra test
COPY tests tests
CMD ["uv", "run", "pytest"]

FROM python:3.14-alpine AS app
WORKDIR /app
# We want to use user/group 1000:1000. Add them properly so they don't cause
# problems.
RUN addgroup -g 1000 clawp
RUN adduser -h /app/clawp_files -G clawp -D -H -u 1000 clawp
COPY --from=build-rust-mcp-filesystem /dist/rust-mcp-filesystem /usr/local/bin/
RUN apk add --no-cache olm
# Copy the .venv and add it to PATH.
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER 1000:1000
