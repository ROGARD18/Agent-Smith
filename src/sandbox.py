import builtins
from typing import List, Dict
from pydantic import BaseModel, Field


class SandboxConfig(BaseModel):
    """Sandbox configuration for student solutions.
    Uses allowlist approach: only imports in authorized_imports are allowed.
    Everything else is blocked by default.
    """
    authorized_imports: List[str] = Field(default_factory=lambda: [
            "math", "math.*",
            "collections", "collections.*",
            "itertools", "re", "json",
            "typing", "typing.*",
            "functools", "operator",
            "heapq", "bisect", "copy",
            "string", "random",
            "datetime", "datetime.*",
            "array", "cmath",
            ])
    allowed_directories: List[str] = Field(default_factory=lambda: [
            "/testbed", "/tmp/agent"
            ])
    max_execution_time_seconds: int = 30
    max_memory_mb: int = 512


class Sandbox:
    def __init__(self, config: SandboxConfig):
        self.config = config

    def _get_safe_globals(self) -> Dict:
        safe_builtins: Dict = {}

        safe_builtins['print'] = getattr(builtins, print)

        return {
            "__builtins__": safe_builtins
        }

    def execute(self, code_string: str) -> None:
        safe_globals: Dict = self._get_safe_globals()
        safe_locals: Dict = {}

        try:
            exec(code_string, safe_globals, safe_locals)
            return ("Code run perfecly.")
        except Exception as e:
            return (f"Code failed ! Error: {e}")
