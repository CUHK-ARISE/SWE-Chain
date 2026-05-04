from __future__ import annotations
import subprocess
import uuid
from typing import Dict, Optional


class DockerContainer:
    def __init__(self, name: str, uid: str = "", gid: str = ""):
        self.name = name
        self.uid = uid
        self.gid = gid
    
    
    @classmethod
    def start(cls, image: str, prefix: str = "swe-test") -> DockerContainer:
        name = f"{prefix}-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["docker", "run", "-d", "--name", name, image, "tail", "-f", "/dev/null"],
            capture_output=True, text=True, check=True,
        )
        uid = subprocess.run(
            ["docker", "exec", name, "id", "-u"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        gid = subprocess.run(
            ["docker", "exec", name, "id", "-g"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return cls(name, uid=uid, gid=gid)
    
    
    def stop(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            capture_output=True, text=True, check=False,
        )
    
    
    def __enter__(self) -> DockerContainer:
        return self
    
    
    def __exit__(self, *exc) -> None:
        self.stop()
    
    
    def exec(self, cmd: str, *, user: Optional[str] = None, env: Optional[Dict] = None, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
        docker_cmd = ["docker", "exec"]
        if user:
            docker_cmd += ["-u", user]
        for key, value in (env or {}).items():
            docker_cmd += ["-e", f"{key}={value}"]
        docker_cmd += [self.name, "bash", "-c", cmd]
        return subprocess.run(
            docker_cmd, capture_output=True, text=True,
            check=check, timeout=timeout,
        )
    
    
    def cp_to(self, local_path: str, container_path: str) -> None:
        subprocess.run(
            ["docker", "cp", local_path, f"{self.name}:{container_path}"],
            capture_output=True, text=True, check=True,
        )
        
    
    def cp_from(self, container_path: str, local_path: str) -> None:
        subprocess.run(
            ["docker", "cp", f"{self.name}:{container_path}", local_path],
            capture_output=True, text=True, check=True,
        )
    
    
    def file_exists(self, path: str) -> bool:
        r = self.exec(f"test -f {path}", check=False)
        return r.returncode == 0
    
    
    def chown(self, path: str, recursive: bool = True) -> None:
        flag = "-R " if recursive else ""
        self.exec(f"chown {flag}{self.uid}:{self.gid} {path}", user="root")
