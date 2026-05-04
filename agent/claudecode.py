import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from agent.agent_runner import AgentRunner
from generate.container import DockerContainer


RULES_PATH = Path(__file__).with_name("claudecode-settings.json")
CONTAINER_HOME = "/home/agent/.claude"
CONTAINER_RULES_PATH = f"{CONTAINER_HOME}/settings.json"


class ClaudeCodeRunner(AgentRunner):
    name = "Claude-Code"
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")

    def extract_session_id(self, trajectory: List[Dict]) -> Optional[str]:
        for event in trajectory:
            sid = event.get("session_id") or event.get("sessionId")
            if sid:
                return sid
        return None
    
    
    def extract_tool_fingerprint(self, event: Dict) -> Optional[str]:
        part = event.get("part")
        if isinstance(part, dict):
            tool = part.get("tool")
            state = part.get("state")
            if tool and isinstance(state, dict):
                input_data = state.get("input", {})
                return f"{tool}:{json.dumps(input_data, sort_keys=True)}"
        return None
    
    
    def extract_token_usage(self, trajectory: List[Dict]) -> Dict[str, int]:
        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for event in trajectory:
            if event.get("type") != "assistant":
                continue
            usage = event.get("message", {}).get("usage", {})
            totals["input"] += usage.get("input_tokens", 0)
            totals["output"] += usage.get("output_tokens", 0)
            totals["cache_read"] += usage.get("cache_read_input_tokens", 0)
            totals["cache_write"] += usage.get("cache_creation_input_tokens", 0)
        return totals


    def setup_container(self, container: DockerContainer) -> None:
        print("  Setting up Claude Code CLI in container...")
        if container.exec("which claude", check=False).returncode != 0:
            container.exec(
                "apt-get update && "
                "apt-get install -y curl ca-certificates gnupg && "
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && "
                "apt-get install -y nodejs && "
                "npm install -g @anthropic-ai/claude-code"
            )
        container.exec(f"mkdir -p {CONTAINER_HOME}")
        container.cp_to(str(RULES_PATH), CONTAINER_RULES_PATH)
        self.install_hosts_blacklist(container)
        container.exec(f"chown -R agent:agent {CONTAINER_HOME} 2>/dev/null; true")
    
    
    def run(self, prompt: str, container: DockerContainer, live_log_path: str) -> Dict[str, Any]:
        print(f"  Running {self.model} on Claude Code with Timeout: {self.timeout}s")
        cmd = [
            "docker", "exec",
            "-u", "agent",
            "-e", "HOME=/home/agent",
            "-e", f"CLAUDE_CODE_OAUTH_TOKEN={self.oauth}",
            "-w", "/app",
            container.name,
            "claude", "-p",
            prompt,
            "--model", self.model,
            "--output-format", "stream-json",
            "--verbose",
            "--settings", CONTAINER_RULES_PATH,
            "--permission-mode", "bypassPermissions",
        ]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        if self.effort:
            cmd += ["--effort", self.effort]
        return self.execute(cmd, live_log_path=live_log_path)
