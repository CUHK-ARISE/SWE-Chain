import json
import subprocess
from pathlib import Path


class CodexCaller:
    def __init__(self, model: str, timeout: int = 1800):
        self.model = model
        self.timeout = timeout
        self.sessions: dict[str, str] = {}
    
    def run(self, prompt: str, workspace: Path, session_key: str = None) -> None:
        cmd = [
            "codex", "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--model", self.model,
            "-c", 'model_reasoning_effort="xhigh"',
        ]
        session_id = self.sessions.get(session_key) if session_key else None
        if session_id:
            cmd[2:2] = ["resume"]
            cmd += [session_id, "-"]
        else:
            cmd.append("-")
        
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        
        if proc.returncode != 0:
            details = proc.stderr.strip() or proc.stdout.strip()
            raise RuntimeError(f"codex exited with code {proc.returncode}: {details[:500]}")
        
        if not session_key:
            return
        
        for line in proc.stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            
            session_id = payload.get("session_id") or payload.get("thread_id")
            if isinstance(session_id, str) and session_id:
                self.sessions[session_key] = session_id
                return
