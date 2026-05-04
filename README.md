# SWE-Chain
SWE-Chain evaluates coding agents (Claude Code, OpenCode, Codex) on multi-step package version upgrades. An agent receives upgrade specs for consecutive version pairs (e.g. Flask 2.0.0 → 2.0.1 → ... → 2.3.3) and must implement each step inside a Docker-isolated environment. Results are scored against the target version's test suite.
