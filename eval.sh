#!/usr/bin/env bash
set -euo pipefail

RESUME=0
ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--resume" ]]; then
        RESUME=1
    else
        ARGS+=("$arg")
    fi
done
set -- ${ARGS[@]+"${ARGS[@]}"}

if [[ $# -lt 1 || $# -gt 3 ]]; then
    echo "Usage: bash eval.sh <chain.json> [workers] [curr-source] [--resume]"
    echo "  chain.json   Path to results/.../chain.json"
    echo "  workers      Parallel replay workers (default: 2)"
    echo "  curr-source  'replay' (default) or 'live' — which jsonl to use as curr for scoring"
    echo "  --resume     Resume from existing replay_{curr,prev}_results.jsonl (default: start fresh)"
    exit 1
fi

FILEPATH="$1"
WORKERS="${2:-2}"
CURR_SOURCE="${3:-replay}"

if [[ ! -f "$FILEPATH" ]]; then
    echo "Error: file not found: $FILEPATH"
    exit 1
fi

if ! [[ "$WORKERS" =~ ^[0-9]+$ ]] || [[ "$WORKERS" -lt 1 ]]; then
    echo "Error: workers must be a positive integer. Got: $WORKERS"
    exit 1
fi

if [[ "$CURR_SOURCE" != "replay" && "$CURR_SOURCE" != "live" ]]; then
    echo "Error: curr-source must be 'replay' or 'live'. Got: $CURR_SOURCE"
    exit 1
fi

DIR="$(dirname "$FILEPATH")"

# Stage 1: replay — rebuild codebases and run cross-tests.
# mode=both when scoring with replay_curr; mode=prev when scoring with live.
if [[ "$CURR_SOURCE" == "replay" ]]; then
    MODE="both"
    CURR_JSONL="$DIR/replay_curr_results.jsonl"
else
    MODE="prev"
    CURR_JSONL="$DIR/live_results.jsonl"
fi

RESUME_ARG=()
if [[ "$RESUME" -eq 1 ]]; then
    RESUME_ARG=(--resume)
fi

python -m evaluation.chain_replay \
    --chain-results "$FILEPATH" \
    --mode "$MODE" \
    --max-workers "$WORKERS" \
    ${RESUME_ARG[@]+"${RESUME_ARG[@]}"}

# Stage 2: compute — score from curr + prev jsonls.
python -m evaluation.chain_compute \
    --chain-results "$FILEPATH" \
    --curr-results "$CURR_JSONL" \
    --prev-results "$DIR/replay_prev_results.jsonl"
