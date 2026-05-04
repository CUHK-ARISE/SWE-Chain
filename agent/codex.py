from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.agent_runner import AgentRunner
from generate.container import DockerContainer


RULES_PATH = Path(__file__).with_name("codex-default.rules")
CONTAINER_HOME = "/home/agent/.codex"
CONTAINER_RULES_PATH = f"{CONTAINER_HOME}/rules/default.rules"

class CodexRunner(AgentRunner):
    name = "Codex"

    def extract_session_id(self, trajectory: List[Dict]) -> Optional[str]:
        for event in trajectory:
            for key in ("thread_id", "session_id"):
                value = event.get(key)
                if isinstance(value, str) and value:
                    return value
            payload = event.get("payload", {})
            if isinstance(payload, dict):
                for key in ("id", "thread_id", "session_id"):
                    value = payload.get(key)
                    if isinstance(value, str) and value:
                        return value
        return None
    
    
    def extract_tool_fingerprint(self, event: Dict) -> Optional[str]:
        item = event.get("item")
        if isinstance(item, dict):
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "tool_call":
                    return f"{content.get('name', '')}:{content.get('arguments', '')}"
        return None
    
    
    def extract_token_usage(self, trajectory: List[Dict]) -> Dict[str, int]:
        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for event in trajectory:
            usage = event.get("usage")
            if not isinstance(usage, dict):
                continue
            totals["input"] += usage.get("input_tokens", 0)
            totals["output"] += usage.get("output_tokens", 0)
            totals["cache_read"] += usage.get("cached_input_tokens", 0)
        return totals


    def setup_container(self, container: DockerContainer) -> None:
        print("  Setting up Codex CLI in container...")
        if container.exec("which codex", check=False).returncode != 0:
            container.exec(
                "apt-get update && "
                "apt-get install -y curl ca-certificates gnupg && "
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && "
                "apt-get install -y nodejs && "
                "npm install -g @openai/codex"
            )
        container.exec(f"mkdir -p {CONTAINER_HOME}/rules")
        local_codex_home = Path.home() / ".codex"
        for name in ("auth.json", "config.toml"):
            source = local_codex_home / name
            if source.exists():
                container.cp_to(str(source), f"{CONTAINER_HOME}/{name}")
        container.cp_to(str(RULES_PATH), CONTAINER_RULES_PATH)
        self.install_hosts_blacklist(container)
        container.exec(f"chown -R agent:agent {CONTAINER_HOME} /app 2>/dev/null; true", check=False)
    
    
    def run(self, prompt: str, container: DockerContainer, live_log_path: str) -> Dict[str, Any]:
        print(f"  Running {self.model} on Codex with Timeout: {self.timeout}s")
        cmd = [
            "docker", "exec", "-i",
            "-u", "agent",
            "-e", "HOME=/home/agent",
            "-e", f"CODEX_HOME={CONTAINER_HOME}",
            "-w", "/app",
            container.name,
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "--model", self.model,
            "-c", 'web_search="disabled"',
            "exec", "--cd", "/app", "--json", "--color", "never",
            "--skip-git-repo-check",
        ]
        if self.effort:
            cmd += ["-c", f'model_reasoning_effort="{self.effort}"']
        if self.session_id:
            cmd += ["resume", self.session_id, "-"]
        else:
            cmd.append("-")
        return self.execute(cmd, input_text=prompt, live_log_path=live_log_path)
