import builtins
import os
import io
import sys
import socket
import resource
import multiprocessing
import queue
import time
import traceback
import ast
from typing import List, Dict, Callable, Any, Optional
from pydantic import BaseModel, Field
from contextlib import redirect_stderr, redirect_stdout

class SandboxConfig(BaseModel):
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
    max_execution_time_seconds: int = 30
    max_memory_mb: int = 512

class Sandbox:
    def __init__(self, config: SandboxConfig, mcp_tools: Optional[Dict[str, Callable]] = None):
        self.config = config
        self.mcp_tools = mcp_tools or {}

    @staticmethod
    def _get_safe_globals(config: SandboxConfig) -> Dict:
        safe_builtins: Dict = builtins.__dict__.copy()
        
        # CORRIGÉ 2.2 : On restaure type, getattr, setattr, delattr et __build_class__ 
        # pour ne pas casser la POO et le fonctionnement normal de Python.
        dangerous_builtins = {'eval', 'exec', 'compile', 'globals', 'locals', 'vars', 'input'}
        for name in dangerous_builtins:
            safe_builtins.pop(name, None)

        original_import = builtins.__import__

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            allowed = False
            for auth_import in config.authorized_imports:
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

        original_open = builtins.open

        def safe_open(file, mode="r", buffering=-1, encoding=None,
                      errors=None, newline=None, closefd=True, opener=None):
            abs_path = os.path.realpath(file)
            is_allowed = any(
                abs_path.startswith(os.path.realpath(d))
                for d in config.allowed_directories
            )
            if not is_allowed:
                raise PermissionError(f"Access denied to the repertory : {file}")
            return original_open(
                file, mode, buffering, encoding, errors,
                newline, closefd, opener
            )

        safe_builtins["__import__"] = safe_import
        safe_builtins["open"] = safe_open

        return {"__builtins__": safe_builtins}

    @staticmethod
    def _disable_network():
        def disabled_socket(*args, **kwargs):
            raise PermissionError("Network access is disabled in the sandbox.")
        socket.socket = disabled_socket
        socket.create_connection = disabled_socket
        socket.socketpair = disabled_socket
        socket.getaddrinfo = disabled_socket

    @staticmethod
    def _worker(config: SandboxConfig, tool_names: List[str], code_string: str, request_queue: multiprocessing.Queue, response_queue: multiprocessing.Queue):
        capture_output = io.StringIO()
        try:
            # --- CORRIGÉ 2.2 : VÉRIFICATION AST POUR BLOQUER L'ÉVASION ---
            # Bloque l'accès aux attributs magiques permettant de remonter l'arbre d'exécution
            class SecurityNodeVisitor(ast.NodeVisitor):
                def visit_Attribute(self, node):
                    if node.attr in ('__class__', '__subclasses__', '__bases__', '__mro__', '__globals__', '__builtins__'):
                        raise PermissionError(f"Security: Access to restricted attribute '{node.attr}' is forbidden.")
                    self.generic_visit(node)
                def visit_Name(self, node):
                    if node.id in ('__class__', '__subclasses__', '__bases__', '__mro__', '__globals__'):
                        raise PermissionError(f"Security: Access to restricted identifier '{node.id}' is forbidden.")
                    self.generic_visit(node)

            try:
                tree = ast.parse(code_string)
                SecurityNodeVisitor().visit(tree)
            except SyntaxError as e:
                request_queue.put({"type": "finish", "result": {"status": "error", "data": f"SyntaxError: {e}"}})
                return
            except PermissionError as e:
                request_queue.put({"type": "finish", "result": {"status": "error", "data": f"SecurityException: {e}"}})
                return
            # -------------------------------------------------------------

            mem_bytes = config.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (config.max_execution_time_seconds, config.max_execution_time_seconds),
            )

            Sandbox._disable_network()
            safe_globals = Sandbox._get_safe_globals(config)

            for tool_name in tool_names:
                def make_stub(name):
                    def stub(*args, **kwargs):
                        request_queue.put({
                            "type": "tool_call",
                            "name": name,
                            "args": args,
                            "kwargs": kwargs
                        })
                        response = response_queue.get()
                        if response["status"] == "error":
                            raise Exception(response["result"])
                        return response["result"]
                    return stub
                safe_globals[tool_name] = make_stub(tool_name)

            def final_answer(solution) -> None:
                request_queue.put({
                    "type": "finish", 
                    "result": {"status": "final_answer", "data": solution}
                })
                sys.exit(0) 

            safe_globals['final_answer'] = final_answer

            with (redirect_stdout(capture_output),
                  redirect_stderr(capture_output)):
                exec(code_string, safe_globals)

            observation = capture_output.getvalue()
            if not observation:
                observation = "Code executed successfully without any output."
            request_queue.put({"type": "finish", "result": {"status": "observation", "data": observation}})

        except SystemExit:
            pass  
        except Exception as e:
            obs = capture_output.getvalue()
            error_msg = f"{type(e).__name__}: {e}\nOutput before error:\n{obs}"
            request_queue.put({"type": "finish", "result": {"status": "error", "data": error_msg}})

    def execute(self, code_string: str) -> Dict[str, Any]:
        request_queue = multiprocessing.Queue()
        response_queue = multiprocessing.Queue()
        
        process = multiprocessing.Process(
            target=Sandbox._worker, 
            args=(self.config, list(self.mcp_tools.keys()), code_string, request_queue, response_queue)
        )
        process.start()

        time_budget = float(self.config.max_execution_time_seconds)
        start_time = time.time()

        while True:
            try:
                msg = request_queue.get(timeout=max(0.1, time_budget))

                if msg["type"] == "tool_call":
                    elapsed = time.time() - start_time
                    time_budget -= elapsed

                    tool_name = msg["name"]
                    try:
                        tool_func = self.mcp_tools[tool_name]
                        result = tool_func(*msg["args"], **msg["kwargs"])
                        response_queue.put({"status": "success", "result": result})
                    except Exception as e:
                        response_queue.put({"status": "error", "result": str(e)})
                    
                    start_time = time.time()

                elif msg["type"] == "finish":
                    process.join(1)
                    return msg["result"]

            except queue.Empty:
                if process.is_alive():
                    process.terminate()
                    process.join(1)
                    if process.is_alive():
                        process.kill()
                return {
                    "status": "error", 
                    "data": f"TimeoutException - Max execution time reached ({self.config.max_execution_time_seconds}s of python execution)."
                }