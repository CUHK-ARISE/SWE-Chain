import subprocess
from typing import List, Optional


class DockerContainer:
    def __init__(self, name: str):
        self.name = name
    
    @classmethod
    def start(cls, image: str, name: str, extra_args: Optional[List[str]] = None) -> "DockerContainer":
        cmd = [
            "docker", "run", "-d", "--name", name,
            *(extra_args or []),
            image, "tail", "-f", "/dev/null",
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            if detail:
                raise RuntimeError(f"docker run failed for container {name}: {detail}") from exc
            raise
        print(f"  Started container: {name} (image: {image})")
        return cls(name)
    
    
    @classmethod
    def attach(cls, container_id: str) -> "DockerContainer":
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True, text=True, check=True,
        )
        if result.stdout.strip() != "true":
            subprocess.run(["docker", "start", container_id], capture_output=True, text=True, check=True)
            print(f"  Restarted stopped container: {container_id}")
        else:
            print(f"  Attached to running container: {container_id}")
        return cls(container_id)
    
    
    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, text=True, check=False)
        print(f"  Removed container: {self.name}")
    
    
    def exec(self, cmd: str, workdir: Optional[str] = None, check: bool = True, user: Optional[str] = "root") -> subprocess.CompletedProcess:
        docker_cmd = ["docker", "exec"]
        if user:
            docker_cmd += ["-u", user]
        if workdir:
            docker_cmd += ["-w", workdir]
        docker_cmd += [self.name, "bash", "-c", cmd]
        return subprocess.run(docker_cmd, capture_output=True, text=True, check=check)
    
    
    def cp_to(self, local_path: str, container_path: str) -> None:
        cmd = ["docker", "cp", local_path, f"{self.name}:{container_path}"]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    
    
    def cp_from(self, container_path: str, local_path: str) -> None:
        cmd = ["docker", "cp", f"{self.name}:{container_path}", local_path]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
