FROM python:3.12-slim
ENV TZ=UTC PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Alpaca CLI is a hard dependency (plan.md C2) -- cli_bridge.health() fails
# and the agent correctly halts without it, rather than trading blind.
# Pinned to v0.0.14 (not `latest`): cli_bridge encodes a real behavioural fact
# about this exact version -- it has no `--output json` flag (memory.md,
# Day 2) -- and a `latest` that moves between now and a later rebuild would
# change CLI output shape under a running agent (docs/day3-llm-plan.md S1d).
# Releases are versioned tarballs (cli_<ver>_linux_amd64.tar.gz), not a bare
# binary at a stable URL -- verified against the actual GitHub release assets.
RUN curl -fsSL -o /tmp/cli.tar.gz \
      https://github.com/alpacahq/cli/releases/download/v0.0.14/cli_0.0.14_linux_amd64.tar.gz \
 && tar -xzf /tmp/cli.tar.gz -C /usr/local/bin alpaca \
 && chmod +x /usr/local/bin/alpaca \
 && rm /tmp/cli.tar.gz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent ./agent

ENV AGENT_DB_PATH=/data/agent.db ALPACA_CLI_PATH=/usr/local/bin/alpaca
# /data is a Railway Volume mounted at deploy time, not a Docker VOLUME --
# Railway's builder rejects the VOLUME instruction outright.

CMD ["python", "-m", "agent.main"]
