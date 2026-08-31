import builtins
import os
import io
import sys
import socket
import resource
import multiprocessing
from typing import List, Dict, Callable, Any, Optional
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
    def __init__(self, config: SandboxConfig, mcp_tools: Optional[Dict[str, Callable]] = None):
        self.config = config
        self.mcp_tools = mcp_tools or {}

    def _get_safe_globals(self) -> Dict:
        safe_builtins: Dict = {}
        
        # 1. Populate standard harmless builtins to prevent NameErrors
        dangerous_builtins = {'eval', 'exec', 'compile', 'globals', 'locals', 'vars', 'input'}
        for name, obj in builtins.__dict__.items():
            if name not in dangerous_builtins:
                safe_builtins[name] = obj

        # 2. Secure Import Function
        original_import = builtins.__import__

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            allowed = False
            for auth_import in self.config.authorized_imports:
                if auth_import.endswith(".*"):
                    base_module = auth_import[:-2]
                    if (name == base_module or name.startswith(base_module + ".")):
                        allowed = True
                        break
                elif name == auth_import:
                    allowed = True
                    break

            if not allowed:
                raise ImportError(f"Import denied: '{name}'.")
            return original_import(name, globals, locals, fromlist, level)

        # 3. Secure Open Function
        original_open = builtins.open

        def safe_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
            # Use realpath to resolve symlinks and relative traversals like '../'
            abs_path = os.path.realpath(file)
            is_allowed = any(
                abs_path.startswith(os.path.realpath(d))
                for d in self.config.allowed_directories
            )
            if not is_allowed:
                raise PermissionError(f"Access denied to the repertory : {file}")
            return original_open(
                file, mode, buffering, encoding, errors,
                newline, closefd, opener
            )

        safe_builtins["__import__"] = safe_import
        safe_builtins["open"] = safe_open

        # Construct globals and inject MCP tools
        safe_globals = {"__builtins__": safe_builtins}
        safe_globals.update(self.mcp_tools)

        return safe_globals

    def _disable_network(self):
        """Overrides socket creation to block network access."""
        def disabled_socket(*args, **kwargs):
            raise PermissionError("Network access is disabled in the sandbox.")
        socket.socket = disabled_socket

    def _worker(self, code_string: str, queue: multiprocessing.Queue):
        capture_sortie = io.StringIO()
        try:
            # Apply memory and CPU limits
            mem_bytes = self.config.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (self.config.max_execution_time_seconds, self.config.max_execution_time_seconds),
            )

            # Apply Network constraints
            self._disable_network()

            # Prepare environment
            safe_globals = self._get_safe_globals()

            # Inject final_answer to signal loop termination
            def final_answer(solution) -> None:
                # We put the dictionary in the queue and exit to stop execution
                queue.put({"status": "final_answer", "data": solution})
                sys.exit(0) 

            safe_globals['final_answer'] = final_answer

            capture_sortie = io.StringIO()

            with redirect_stdout(capture_sortie), redirect_stderr(capture_sortie):
                exec(code_string, safe_globals, {})

            # If execution reaches here, final_answer wasn't called. Return observation.
            observation = capture_sortie.getvalue()
            if not observation:
                observation = "Code executed successfully without any output."
            queue.put({"status": "observation", "data": observation})

        except SystemExit:
            # final_answer triggers this cleanly, ignore unless queue is empty
            if queue.empty():
                queue.put({"status": "error", "data": "Process exited unexpectedly."})
        except BaseException as e:
            # Catch BaseException to catch SystemExit/KeyboardInterrupt if raised maliciously
            obs = capture_sortie.getvalue()
            error_msg = f"{type(e).__name__}: {e}\nOutput before error:\n{obs}"
            queue.put({"status": "error", "data": error_msg})

    def execute(self, code_string: str) -> str:
        queue = multiprocessing.Queue()
        process = multiprocessing.Process(
            target=self._worker, args=(code_string, queue)
        )

        process.start()
        process.join(self.config.max_execution_time_seconds + 1)

        if process.is_alive():
            process.terminate() # Send SIGTERM
            process.join(1)
            if process.is_alive():
                process.kill() # Send SIGKILL if it refuses to die
            return {
                "status": "error", 
                "data": "TimeoutException - Max execution time reached."
            }

        if not queue.empty():
            return queue.get()

        return {"status": "error", "data": "Process crashed unexpectedly without output."}