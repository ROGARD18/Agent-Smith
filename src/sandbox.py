import builtins
import os
import io
import resource
import multiprocessing
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from contextlib import redirect_stderr, redirect_stdout


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
            "array", "cmath", "time"
            ])
    allowed_directories: List[str] = Field(default_factory=lambda: [
            "/testbed", "/tmp/agent"
            ])
    max_execution_time_seconds: int = 1
    max_memory_mb: int = 512


class Sandbox:
    def __init__(self, config: SandboxConfig):
        self.config = config

    def _get_safe_globals(self) -> Dict:
        safe_builtins: Dict = {}
        safe_builtins["print"] = getattr(builtins, "print")

        original_import = builtins.__import__

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            allowed = False
            for auth_import in self.config.authorized_imports:
                if auth_import.endswith(".*"):
                    base_module = auth_import[:-2]
                    if (name == base_module
                            or name.startswith(base_module + ".")):
                        allowed = True
                        break
                elif name == auth_import:
                    allowed = True
                    break

            if not allowed:
                raise ImportError(f"Import denied : '{name}'.")
            return original_import(name, globals, locals, fromlist, level)

        original_open = builtins.open

        def safe_open(
            file,
            mode="r",
            buffering=-1,
            encoding=None,
            errors=None,
            newline=None,
            closefd=True,
            opener=None,
        ):
            abs_path = os.path.abspath(file)
            is_allowed = any(
                abs_path.startswith(os.path.abspath(d))
                for d in self.config.allowed_directories
            )
            if not is_allowed:
                raise PermissionError("Access denied to the "
                                      f"repertory : {file}")
            return original_open(
                file, mode, buffering, encoding, errors,
                newline, closefd, opener
            )

        safe_builtins["__import__"] = safe_import
        safe_builtins["open"] = safe_open

        return {"__builtins__": safe_builtins}

    def _worker(self, code_string: str, queue: multiprocessing.Queue):
        try:
            mem_bytes = self.config.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

            resource.setrlimit(
                resource.RLIMIT_CPU,
                (
                    self.config.max_execution_time_seconds,
                    self.config.max_execution_time_seconds,
                ),
            )

            safe_globals = self._get_safe_globals()

            def final_answer(solution) -> None:
                print(f"[FINAL ANSWER] : {solution}")

            safe_globals['final_answer'] = final_answer

            capture_sortie = io.StringIO()

            with (redirect_stdout(capture_sortie),
                  redirect_stderr(capture_sortie)):
                exec(code_string, safe_globals, {})

            observation = capture_sortie.getvalue()
            if observation == "":
                observation = "Code executed without errors"
            queue.put(observation)

        except Exception as e:
            queue.put(f"Code failed ! Error: {type(e).__name__} - {e}")

    def execute(self, code_string: str) -> str:
        queue: Any = multiprocessing.Queue()
        process = multiprocessing.Process(
            target=self._worker, args=(code_string, queue)
        )

        process.start()
        process.join(self.config.max_execution_time_seconds + 1)

        if process.is_alive():
            process.terminate()
            process.join()
            return ("Code failed ! Error: TimeoutException - Max "
                    "execution time reached.")

        if not queue.empty():
            return queue.get()

        return "Code failed ! Error: Process crashed unexpectedly."
