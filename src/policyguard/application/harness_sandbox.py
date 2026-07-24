"""Docker-isolated code execution contract with deny-by-default permissions."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    image: str = "python:3.12-alpine"
    timeout_seconds: int = 10
    memory_mb: int = 128
    cpus: float = 0.5
    pids_limit: int = 32
    max_output_bytes: int = 64_000
    network_enabled: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= 60:
            raise ValueError("sandbox_timeout_out_of_range")
        if not 32 <= self.memory_mb <= 1024:
            raise ValueError("sandbox_memory_out_of_range")
        if not 0.1 <= self.cpus <= 2:
            raise ValueError("sandbox_cpu_out_of_range")


def docker_command(workspace: Path, policy: SandboxPolicy) -> list[str]:
    command = [
        "docker", "run", "--rm", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", f"--memory={policy.memory_mb}m",
        f"--cpus={policy.cpus}", f"--pids-limit={policy.pids_limit}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--mount", f"type=bind,src={workspace.resolve()},dst=/workspace,readonly",
        "--workdir", "/workspace",
    ]
    command += ["--network=bridge"] if policy.network_enabled else ["--network=none"]
    command += [policy.image, "python", "-I", "task.py"]
    return command


class DockerSandboxExecutor:
    def __init__(self, root: Path, policy: SandboxPolicy, *, enabled: bool = False) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.enabled = enabled

    def readiness(self) -> dict:
        docker = shutil.which("docker")
        return {
            "enabled": self.enabled,
            "docker_available": docker is not None,
            "network": "enabled" if self.policy.network_enabled else "disabled",
            "image": self.policy.image,
        }

    def execute_python(self, code: str) -> dict:
        if not self.enabled:
            raise RuntimeError("sandbox_execution_disabled")
        if shutil.which("docker") is None:
            raise RuntimeError("sandbox_docker_unavailable")
        if len(code.encode("utf-8")) > 32_000:
            raise ValueError("sandbox_code_too_large")
        with tempfile.TemporaryDirectory(prefix="run-", dir=self.root) as temporary:
            workspace = Path(temporary).resolve()
            (workspace / "task.py").write_text(code, encoding="utf-8")
            started = perf_counter()
            try:
                completed = subprocess.run(
                    docker_command(workspace, self.policy),
                    capture_output=True,
                    timeout=self.policy.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("sandbox_timeout") from exc
            stdout = completed.stdout[: self.policy.max_output_bytes]
            stderr = completed.stderr[: self.policy.max_output_bytes]
            return {
                "exit_code": completed.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "stdout_truncated": len(completed.stdout) > len(stdout),
                "stderr_truncated": len(completed.stderr) > len(stderr),
                "latency_ms": round((perf_counter() - started) * 1000, 2),
                "network_enabled": self.policy.network_enabled,
            }
