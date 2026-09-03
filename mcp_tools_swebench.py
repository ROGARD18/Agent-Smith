import os
import subprocess
import argparse
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SWEBench-Tools")

def _run_shell(cmd: str) -> str:
    """Helper to route commands locally or via Docker exec."""
    testbed = os.environ.get("TESTBED_PATH", "/testbed")
    container = os.environ.get("SWE_CONTAINER_NAME")

    if container:
        # Bridge into Docker
        full_cmd = ["docker", "exec", "-w", testbed, container, "sh", "-c", cmd]
    else:
        # Run locally (used by moulinette tool isolation tests)
        full_cmd = ["sh", "-c", f"cd {testbed} && {cmd}"]

    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        return output if output else "Command executed successfully (no output)."
    except subprocess.TimeoutExpired:
        return "Error: Command timed out."
    except Exception as e:
        return f"Error executing command: {e}"

@mcp.tool()
def run_command(command: str) -> str:
    """Executes a generic shell command in the repository environment."""
    return _run_shell(command)

@mcp.tool()
def read_file(filepath: str) -> str:
    """
    Reads a file and prints it with 1-indexed lines. 
    Uses `cat -n` style as requested by the subject.
    """
    return _run_shell(f"cat -n {filepath}")

@mcp.tool()
def edit_file(filepath: str, start_line: int, end_line: int, replacement_text: str) -> str:
    """
    Replaces a specific block of lines in a file.
    This bridge uses awk/sed logic or a Python script pushed to the container to handle the edit safely.
    For simplicity, rewriting via a temporary patch or shell trick works.
    """
    # A robust way to edit files over a docker exec bridge without mounting:
    # We write a quick Python edit script and execute it via the shell.
    py_script = f"""
import sys
with open('{filepath}', 'r') as f: lines = f.readlines()
lines[{start_line-1}:{end_line}] = [l + '\\n' for l in '''{replacement_text}'''.split('\\n')]
with open('{filepath}', 'w') as f: f.writelines(lines)
"""
    # Escape single quotes for the shell wrap
    escaped_script = py_script.replace("'", "'\\''")
    return _run_shell(f"python3 -c '{escaped_script}'")

@mcp.tool()
def get_patch() -> str:
    """Generates the final solution patch."""
    return _run_shell("git -c core.fileMode=false diff")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SWE-bench MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", port=args.port)