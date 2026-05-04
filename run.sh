#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 || $# -gt 6 ]]; then
    echo "Usage bash run.sh <data> <agent> <provider> <model> [effort] [container-id]"
    echo "  data:                   Path to JSONL data file (e.g., data/flask_chain.jsonl)"
    echo "  agent:                  Agent CLI (opencode, claudecode, or codex)"
    echo "  provider:               LLM provider (e.g., anthropic, openai)"
    echo "  model:                  LLM model name (e.g., claude-sonnet-4-20250514, gpt-4o)"
    echo "  effort:                 Reasoning effort (e.g., low, medium, high, max)"
    echo "  container-id:           Attach to existing container to continue (implies --resume)"
    exit 1
fi

DATA="$1"
AGENT="$2"
PROVIDER="$3"
MODEL="$4"
EFFORT="${5:-}"
CONTAINER_ID="${6:-}"

CMD=(
    python -m generate.chain
    --data "$DATA"
    --agent "$AGENT"
    --provider "$PROVIDER"
    --model "$MODEL"
    --resume
)
if [[ -n "$EFFORT" ]]; then
    CMD+=(--effort "$EFFORT")
fi
if [[ -n "$CONTAINER_ID" ]]; then
    CMD+=(--container-id "$CONTAINER_ID")
fi

"${CMD[@]}"
