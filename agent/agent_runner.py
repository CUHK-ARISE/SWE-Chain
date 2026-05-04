import json
import select
import subprocess
import time
from abc import ABC, abstractmethod
from collections import Counter
from typing import Dict, Any, List, Optional

from generate.container import DockerContainer

DEFAULT_TIMEOUT = 1800
HEARTBEAT_INTERVAL = 30
POLL_INTERVAL = 1.0
REPETITION_LIMIT = 50

BLOCKED_HOSTS = [
    "raw.githubusercontent.com",
    "github.com",
    "api.github.com",
    "gist.githubusercontent.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "media.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "gitlab.com",
    "gitlabusercontent.com",
    "bitbucket.org",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "storage.googleapis.com",
    "pastebin.com",
    "hastebin.com",
    "ghostbin.com",
]

class AgentRunner(ABC):
    name: str = "Agent"

    def __init__(self, model: str, timeout: int = DEFAULT_TIMEOUT, effort: Optional[str] = None, **kwargs) -> None:
        self.model = model
        self.timeout = timeout
        self.effort = effort
        self.kwargs = kwargs
        self.session_id: Optional[str] = None


    def extract_session_id(self, trajectory: List[Dict]) -> Optional[str]:
        return None


    def extract_tool_fingerprint(self, event: Dict) -> Optional[str]:
        return None

    def execute(self, cmd: List[str], live_log_path: str, input_text: Optional[str] = None) -> Dict[str, Any]:        
        start = time.time()
        success = True
        error_msg = None
        trajectory: List[Dict] = []
        proc: Optional[subprocess.Popen[str]] = None
        tool_calls: Counter = Counter()

        with open(live_log_path, "a") as log:
            def write_log(event: Dict[str, Any]) -> None:
                log.write(json.dumps(event) + "\n")
                log.flush()
            
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if input_text else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if input_text and proc.stdin:
                    proc.stdin.write(input_text)
                    proc.stdin.close()
                
                next_heartbeat = start + HEARTBEAT_INTERVAL
                while proc.stdout:
                    if time.time() - start > self.timeout:
                        proc.kill()
                        proc.wait()
                        success = False
                        error_msg = f"Timed out after {self.timeout}s"
                        break
                    
                    if not select.select([proc.stdout], [], [], POLL_INTERVAL)[0]:
                        now = time.time()
                        if now >= next_heartbeat:
                            elapsed = round(now - start, 1)
                            heartbeat = {"type": "heartbeat", "timestamp": int(now * 1000),
                                         "agent": self.name, "model": self.model, "elapsed_seconds": elapsed}
                            if self.session_id:
                                heartbeat["sessionID"] = self.session_id
                            print(f"  Heartbeat: waiting for {self.name} output ({elapsed:.1f}s elapsed)")
                            write_log(heartbeat)
                            next_heartbeat = now + HEARTBEAT_INTERVAL
                        continue
                    
                    raw = proc.stdout.readline()
                    if raw == "":
                        if proc.poll() is not None:
                            break
                        continue
                    try:
                        event = json.loads(raw.strip())
                    except (json.JSONDecodeError, ValueError):
                        continue
                    
                    trajectory.append(event)
                    write_log(event)
                    next_heartbeat = time.time() + HEARTBEAT_INTERVAL
                    
                    fp = self.extract_tool_fingerprint(event)
                    if fp:
                        tool_calls[fp] += 1
                        if tool_calls[fp] >= REPETITION_LIMIT:
                            proc.kill(); proc.wait()
                            success = False
                            error_msg = f"Budget protection: same tool call repeated {REPETITION_LIMIT} times — {fp[:120]}"
                            print(f"  {error_msg}")
                            break
                    
                    if not self.session_id:
                        self.session_id = self.extract_session_id([event])
                        if self.session_id:
                            print(f"  Session ID: {self.session_id}")
                
                proc.wait()
                stderr = proc.stderr.read() if proc.stderr else ""
                if proc.returncode != 0 and success:
                    success = False
                    error_msg = f"{self.name} exited with code {proc.returncode}: {stderr[:500]}"
            
            except Exception as e:
                success = False
                error_msg = str(e)
                print(f"  Error running {self.name}: {e}")
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait()
        
        elapsed = time.time() - start
        token_usage = None
        if trajectory:
            print(f"  Trajectory: {len(trajectory)} events recorded")
            token_usage = self.extract_token_usage(trajectory)
            if any(token_usage.values()):
                print(f"  Tokens: {token_usage}")
        
        return {
            "elapsed_seconds": round(elapsed, 1),
            "success": success,
            "error": error_msg,
            "token_usage": token_usage,
            "trajectory": trajectory,
        }


    @abstractmethod
    def extract_token_usage(self, trajectory: List[Dict]) -> Dict[str, int]:
        """Aggregate token usage from trajectory events.

        Returns dict with keys: input, output, cache_read, cache_write.
        """
        ...


    def install_hosts_blacklist(self, container: DockerContainer) -> None:
        blocked_hosts = json.dumps(BLOCKED_HOSTS)
        cmd = (
            "python - <<'PY'\n"
            "import json\n"
            "from pathlib import Path\n"
            "\n"
            "hosts = json.loads('''"
            f"{blocked_hosts}"
            "''')\n"
            "path = Path('/etc/hosts')\n"
            "text = path.read_text()\n"
            "\n"
            "for host in hosts:\n"
            "    line = f'0.0.0.0 {host}'\n"
            "    if line not in text:\n"
            "        if text and not text.endswith('\\n'):\n"
            "            text += '\\n'\n"
            "        text += line + '\\n'\n"
            "\n"
            "path.write_text(text)\n"
            "PY"
        )
        container.exec(cmd)
        print(f"  Installed hosts blacklist with {len(BLOCKED_HOSTS)} entries")

    @abstractmethod
    def setup_container(self, container: DockerContainer) -> None:
        ...


    @abstractmethod
    def run(
        self,
        prompt: str,
        container: DockerContainer,
        live_log_path: str,
    ) -> Dict[str, Any]:
        ...
