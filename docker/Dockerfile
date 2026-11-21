# Clip desk image: admin UI + scheduler + the clip pipeline (headless Claude Code, yt-dlp, ffmpeg, whisper on CPU)
FROM python:3.12-slim-trixie

ARG UID=1000
ARG GID=1000
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 \
    CLIP_DESK_HOME=/data/desk CLAUDE_CONFIG_DIR=/home/desk/.claude-config DESK_SCHEDULER=loop DESK_HOST=0.0.0.0

# Debian's ffmpeg has libass (subtitles); fonts-noto-core has Noto Sans Tamil (and other Indian scripts)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-noto-core fontconfig zsh curl ca-certificates sqlite3 lsof procps tzdata git \
    && rm -rf /var/lib/apt/lists/*

RUN (getent group ${GID} >/dev/null || groupadd -g ${GID} desk) && useradd -o -m -u ${UID} -g ${GID} -s /bin/zsh desk \
    && mkdir -p /app /data/desk && chown -R ${UID}:${GID} /app /data

USER desk
WORKDIR /app

# python deps in /app/.venv (the path hourly.sh expects: $REPO/.venv/bin)
COPY --chown=${UID}:${GID} requirements.txt /app/requirements.txt
RUN python -m venv /app/.venv && /app/.venv/bin/pip install --no-cache-dir -q --upgrade pip \
    && /app/.venv/bin/pip install --no-cache-dir -q -r /app/requirements.txt

# Claude Code CLI -> ~/.local/bin/claude (on hourly.sh's PATH); login is kept in the claude-config volume
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/app/.venv/bin:/home/desk/.local/bin:${PATH}"

COPY --chown=${UID}:${GID} . /app
RUN chmod +x /app/docker/entrypoint.sh /app/run.sh

EXPOSE 8765
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "/app/admin/app.py"]
