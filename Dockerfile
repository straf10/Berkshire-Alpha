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
RUN curl -fsSL -o /usr/local/bin/alpaca \
    https://github.com/alpacahq/cli/releases/download/v0.0.14/alpaca-linux-amd64 \
 && chmod +x /usr/local/bin/alpaca

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent ./agent

ENV AGENT_DB_PATH=/data/agent.db ALPACA_CLI_PATH=/usr/local/bin/alpaca
VOLUME ["/data"]

CMD ["python", "-m", "agent.main"]
