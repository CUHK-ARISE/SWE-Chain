import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from agent.agent_runner import AgentRunner
from generate.container import DockerContainer


RULES_PATH = Path(__file__).with_name("opencode-deny.json")
CONTAINER_HOME = "/home/agent/.local/share/opencode"
CONTAINER_RULES_PATH = "/app/opencode-deny.json"

class OpenCodeRunner(AgentRunner):
    name = "OpenCode"
    
    
    def extract_session_id(self, trajectory: List[Dict]) -> Optional[str]:
        for event in trajectory:
            sid = event.get("sessionID")
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
            part = event.get("part")
            if not isinstance(part, dict) or part.get("type") != "step-finish":
                continue
            tokens = part.get("tokens", {})
            totals["input"] += tokens.get("input", 0)
            totals["output"] += tokens.get("output", 0)
            cache = tokens.get("cache", {})
            totals["cache_read"] += cache.get("read", 0)
            totals["cache_write"] += cache.get("write", 0)
        return totals


    def setup_container(self, container: DockerContainer) -> None:
        print("  Setting up OpenCode CLI in container...")
        if container.exec("which opencode", check=False).returncode != 0:
            container.exec(
                "apt-get update && "
                "apt-get install -y curl ca-certificates gnupg && "
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && "
                "apt-get install -y nodejs && "
                "npm install -g opencode-ai"
            )
        container.exec(f"mkdir -p {CONTAINER_HOME} /home/agent/.local/state/opencode")
        auth_json = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if auth_json.exists():
            container.cp_to(str(auth_json), f"{CONTAINER_HOME}/auth.json")
        container.cp_to(str(RULES_PATH), CONTAINER_RULES_PATH)
        self.install_hosts_blacklist(container)
        container.exec("chown -R agent:agent /home/agent/.local /app 2>/dev/null; true", check=False)
    
    
    def run(self, prompt: str, container: DockerContainer, live_log_path: str) -> Dict[str, Any]:
        print(f"  Running {self.model} on OpenCode with Timeout: {self.timeout}s")
        cmd = [
            "docker", "exec",
            "-u", "agent",
            "-e", "HOME=/home/agent",
            "-e", f"OPENCODE_CONFIG={CONTAINER_RULES_PATH}",
            "-w", "/app",
            container.name,
            "opencode", "run",
            prompt,
            "--model", self.model,
            "--format", "json",
            "--agent", "build",
        ]
        if self.session_id:
            cmd += ["--session", self.session_id]
        if self.effort:
            cmd += ["--variant", self.effort]
        
        return self.execute(cmd, live_log_path=live_log_path)
