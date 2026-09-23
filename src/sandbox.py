import ast
import builtins
import io
import json
import multiprocessing
import os
from pathlib import Path
import queue
import resource
import socket
import sys
import time
from typing import Any, Callable, Dict, List, Optional, cast

from pydantic import BaseModel, Field


class SandboxConfig(BaseModel):
    """Sandbox configuration for student solutions.

    Uses allowlist approach: only imports in authorized_imports are allowed.
    Everything else is blocked by default.
    """

    authorized_imports: List[str] = Field(
        default_factory=lambda: [
            "math",
            "math.*",
            "collections",
            "collections.*",
            "itertools",
            "re",
            "json",
            "typing",
            "typing.*",
            "functools",
            "operator",
            "heapq",
            "bisect",
            "copy",
            "string",
            "random",
            "datetime",
            "datetime.*",
            "array",
            "cmath",
            "time",
        ]
    )
    allowed_directories: List[str] = Field(
        default_factory=lambda: ["/testbed", "/tmp/agent"]
    )
    max_execution_time_seconds: int = 30
    max_memory_mb: int = 512

    @classmethod
    def from_json_file(cls, path: str | Path) -> "SandboxConfig":
        data = json.loads(Path(path).read_text())
        return cls.model_validate(data)


class Sandbox:
    """Secure sandbox for executing LLM-generated Python code.

    Runs code in a separate process with restricted imports, filesystem
    access, network disabled, and timeout/memory limits enforced. MCP tool
    wrappers are injected as callable functions in the sandbox namespace.
    """

    def __init__(
        self,
        config: SandboxConfig,
        mcp_tools: Optional[Dict[str, Callable[..., Any]]] = None,
    ):
        self.config = config
        self.mcp_tools = mcp_tools or {}

    @staticmethod
    def _get_safe_globals(config: SandboxConfig) -> Dict[str, Any]:
        """Build a restricted globals dict with safe builtins."""
        safe_builtins: Dict[str, Any] = builtins.__dict__.copy()

        # Remove dangerous builtins that allow code injection/introspection
        # Keep __build_class__ (needed for class definitions),
        # type (needed for type checks), getattr/setattr (legitimate use)
        dangerous_builtins = {
            "eval",
            "exec",
            "compile",
            "globals",
            "locals",
            "vars",
            "input",
            "breakpoint",
        }
        for name in dangerous_builtins:
            safe_builtins.pop(name, None)

        original_import = builtins.__import__

        def safe_import(
            name: str,
            globals: Any = None,
            locals: Any = None,
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            allowed = False
            for auth_import in config.authorized_imports:
                if auth_import.endswith(".*"):
                    base_module = auth_import[:-2]
                    if (
                        name == base_module
                        or name.startswith(base_module + ".")
                    ):
                        allowed = True
                        break
                elif name == auth_import:
                    allowed = True
                    break
            if not allowed:
                raise ImportError(f"Import denied: '{name}'.")
            return original_import(name, globals, locals, fromlist, level)

        original_open = builtins.open

        def safe_open(
            file: Any,
            mode: str = "r",
            buffering: int = -1,
            encoding: Optional[str] = None,
            errors: Optional[str] = None,
            newline: Optional[str] = None,
            closefd: bool = True,
            opener: Any = None,
        ) -> Any:
            abs_path = os.path.realpath(str(file))
            is_allowed = any(
                abs_path.startswith(os.path.realpath(d))
                for d in config.allowed_directories
            )
            if not is_allowed:
                raise PermissionError(
                    f"Access denied to the directory: {file}"
                )
            return original_open(
                file,
                mode,
                buffering,
                encoding,
                errors,
                newline,
                closefd,
                opener,
            )

        safe_builtins["__import__"] = safe_import
        safe_builtins["open"] = safe_open

        return {"__builtins__": safe_builtins}

    @staticmethod
    def _disable_network() -> None:
        """Disable all network access by monkey-patching socket."""

        def disabled_socket(*args: Any, **kwargs: Any) -> Any:
            raise PermissionError(
                "Network access is disabled in the sandbox."
            )

        socket.socket = disabled_socket  # type: ignore[misc, assignment]
        socket.create_connection = disabled_socket
        socket.socketpair = disabled_socket
        socket.getaddrinfo = disabled_socket

    @staticmethod
    def _worker(
        config: SandboxConfig,
        tool_names: List[str],
        code_string: str,
        request_queue: "multiprocessing.Queue[Any]",
        response_queue: "multiprocessing.Queue[Any]",
    ) -> None:
        """Worker process that executes sandboxed code."""
        capture_output = io.StringIO()
        try:
            # AST-based security check before execution
            class SecurityNodeVisitor(ast.NodeVisitor):
                """Block access to dangerous dunder attributes at AST level."""

                BLOCKED_ATTRS = {
                    "__class__",
                    "__subclasses__",
                    "__bases__",
                    "__mro__",
                    "__globals__",
                    "__builtins__",
                    "__code__",
                    "__func__",
                    "__self__",
                    "__dict__",
                    "__init_subclass__",
                    "__set_name__",
                    "__del__",
                }
                BLOCKED_NAMES = {
                    "__class__",
                    "__subclasses__",
                    "__bases__",
                    "__mro__",
                    "__globals__",
                    "__builtins__",
                }

                def visit_Attribute(self, node: ast.Attribute) -> None:
                    if node.attr in self.BLOCKED_ATTRS:
                        raise PermissionError(
                            f"Security: Access to restricted attribute "
                            f"'{node.attr}' is forbidden."
                        )
                    self.generic_visit(node)

                def visit_Name(self, node: ast.Name) -> None:
                    if node.id in self.BLOCKED_NAMES:
                        raise PermissionError(
                            f"Security: Access to restricted identifier "
                            f"'{node.id}' is forbidden."
                        )
                    self.generic_visit(node)

            try:
                tree = ast.parse(code_string)
                SecurityNodeVisitor().visit(tree)
            except SyntaxError as e:
                request_queue.put(
                    {
                        "type": "finish",
                        "result": {
                            "status": "error",
                            "data": f"SyntaxError: {e}",
                        },
                    }
                )
                return
            except PermissionError as e:
                request_queue.put(
                    {
                        "type": "finish",
                        "result": {
                            "status": "error",
                            "data": f"SecurityException: {e}",
                        },
                    }
                )
                return

            # Set resource limits
            mem_bytes = config.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (
                    config.max_execution_time_seconds,
                    config.max_execution_time_seconds,
                ),
            )

            # Disable network and build safe globals
            Sandbox._disable_network()
            safe_globals = Sandbox._get_safe_globals(config)

            # Inject MCP tool stubs that communicate back to main process
            for tool_name in tool_names:

                def make_stub(name: str) -> Callable[..., Any]:
                    def stub(*args: Any, **kwargs: Any) -> Any:
                        request_queue.put(
                            {
                                "type": "tool_call",
                                "name": name,
                                "args": args,
                                "kwargs": kwargs,
                            }
                        )
                        response = response_queue.get()
                        if response["status"] == "error":
                            raise Exception(response["result"])
                        return response["result"]

                    return stub

                safe_globals[tool_name] = make_stub(tool_name)

            # Inject final_answer
            def final_answer(solution: Any) -> None:
                request_queue.put(
                    {
                        "type": "finish",
                        "result": {"status": "final_answer", "data": solution},
                    }
                )
                sys.exit(0)

            safe_globals["final_answer"] = final_answer

            # Capture stdout/stderr
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = capture_output
            sys.stderr = capture_output

            try:
                exec(code_string, safe_globals)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            observation = capture_output.getvalue()
            if not observation:
                observation = "Code executed successfully without any output."
            request_queue.put(
                {
                    "type": "finish",
                    "result": {"status": "observation", "data": observation},
                }
            )

        except SystemExit:
            # Allow SystemExit to propagate (used by final_answer)
            pass
        except KeyboardInterrupt:
            # Propagate KeyboardInterrupt
            request_queue.put(
                {
                    "type": "finish",
                    "result": {
                        "status": "error",
                        "data": "KeyboardInterrupt",
                    },
                }
            )
        except Exception as e:
            obs = capture_output.getvalue()
            error_msg = f"{type(e).__name__}: {e}"
            if obs:
                error_msg += f"\nOutput before error:\n{obs}"
            request_queue.put(
                {
                    "type": "finish",
                    "result": {"status": "error", "data": error_msg},
                }
            )

    def execute(self, code_string: str) -> Dict[str, Any]:
        """Execute code in a sandboxed subprocess.

        Returns a dict with:
            status: 'observation' | 'error' | 'final_answer'
            data: the output string or solution
        """
        request_queue: multiprocessing.Queue[Any] = multiprocessing.Queue()
        response_queue: multiprocessing.Queue[Any] = multiprocessing.Queue()

        process = multiprocessing.Process(
            target=Sandbox._worker,
            args=(
                self.config,
                list(self.mcp_tools.keys()),
                code_string,
                request_queue,
                response_queue,
            ),
        )
        process.start()

        time_budget = float(self.config.max_execution_time_seconds)
        start_time = time.time()

        while True:
            try:
                msg = request_queue.get(timeout=max(0.1, time_budget))

                if msg["type"] == "tool_call":
                    # Tool calls happen outside the sandbox timeout
                    elapsed = time.time() - start_time
                    time_budget -= elapsed

                    tool_name = msg["name"]
                    try:
                        tool_func = self.mcp_tools[tool_name]
                        result = tool_func(*msg["args"], **msg["kwargs"])
                        response_queue.put(
                            {"status": "success", "result": result}
                        )
                    except Exception as e:
                        response_queue.put(
                            {"status": "error", "result": str(e)}
                        )

                    start_time = time.time()

                elif msg["type"] == "finish":
                    process.join(1)
                    return cast(Dict[str, Any], msg["result"])

            except queue.Empty:
                if process.is_alive():
                    process.terminate()
                    process.join(1)
                    if process.is_alive():
                        process.kill()
                        process.join(1)
                return {
                    "status": "error",
                    "data": (
                        f"TimeoutException - Max execution time reached "
                        f"({self.config.max_execution_time_seconds}s). "
                        f"Output may be partial."
                    ),
                }
