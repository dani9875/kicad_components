# Sandbox for Claude Code (used by library_browser.py).
#
# Build once, from the repo root:
#   docker build -t pepy-claude --build-arg UID=$(id -u) --build-arg GID=$(id -g) docker/
#
# At run time the browser mounts the repo at /work and hides every entry except
#   pepy_sym_lib.kicad_sym, pepy_sym_lib.pretty/, original_components/
# Login and sessions live in the Docker volume "pepy-claude-home", not in your home folder.
FROM node:22-slim

ARG UID=1000
ARG GID=1000
ARG CLAUDE_VERSION=latest

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION} \
 && npm cache clean --force

# Run as your host user so edited files keep their owner.
# node:22-slim already has user "node" with UID/GID 1000; reuse it or create one.
RUN set -eux; \
    if ! getent group "${GID}" >/dev/null; then groupadd -g "${GID}" claude; fi; \
    if getent passwd "${UID}" >/dev/null; then \
        usermod -d /home/claude -m "$(getent passwd "${UID}" | cut -d: -f1)"; \
    else \
        useradd -m -u "${UID}" -g "${GID}" -d /home/claude claude; \
    fi; \
    mkdir -p /work /home/claude/.claude; \
    chown -R "${UID}:${GID}" /home/claude

ENV HOME=/home/claude \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1

USER ${UID}:${GID}
WORKDIR /work
ENTRYPOINT ["claude"]