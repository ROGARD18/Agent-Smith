import os
import re
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# Initialize the FastMCP server
mcp = FastMCP("SWE-bench-Tools")

def get_testbed() -> Path:
    """Retrieve the testbed path from the environment, defaulting to /testbed."""
    testbed_path = os.environ.get("TESTBED_PATH", "/testbed")
    return Path(testbed_path).resolve()

def resolve_safe_path(filepath: str) -> Path:
    """Ensure the path stays within the testbed to prevent path traversal."""
    testbed = get_testbed()
    safe_path = (testbed / filepath.lstrip("/")).resolve()
    if not str(safe_path).startswith(str(testbed)):
        raise PermissionError(f"Path traversal denied: {filepath}")
    return safe_path


@mcp.tool()
def read_file(filepath: str, start_line: int, end_line: int) -> str:
    """Read the content of a file with line numbers."""
    try:
        path = resolve_safe_path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        output = []
        start = max(1, start_line)
        end = min(len(lines), end_line)
        
        for i in range(start, end + 1):
            output.append(f"{i}: {lines[i-1].rstrip('\n')}")
            
        return "\n".join(output)
    except Exception as e:
        return f"Error reading file: {e}"

@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """Replace an exact string in a file with a new string."""
    try:
        path = resolve_safe_path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if old_str not in content:
            return "Error: The exact old_str was not found in the file."
            
        new_content = content.replace(old_str, new_str)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        return f"Successfully updated {filepath}."
    except Exception as e:
        return f"Error editing file: {e}"

@mcp.tool()
def list_files(directory: str, pattern: str) -> str:
    """List files in a directory matching a given pattern."""
    try:
        base_dir = resolve_safe_path(directory)
        files = list(base_dir.rglob(pattern))
        
        if not files:
            return "No files found."
        return "\n".join(str(f.relative_to(get_testbed())) for f in files if f.is_file())
    except Exception as e:
        return f"Error listing files: {e}"

@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """Perform a grep-like search in the codebase."""
    try:
        testbed = get_testbed()
        files = list(testbed.rglob(file_pattern))
        regex = re.compile(pattern)
        results = []
        
        for f in files:
            if f.is_file():
                try:
                    with open(f, "r", encoding="utf-8") as file_obj:
                        for i, line in enumerate(file_obj, 1):
                            if regex.search(line):
                                # Output must be: /absolute/path_to_file.py:<line_number> <line_content>
                                results.append(f"{f.absolute()}:{i} {line.rstrip('\n')}")
                except UnicodeDecodeError:
                    continue  # Skip binary files
                    
        return "\n".join(results) if results else "No matches found."
    except Exception as e:
        return f"Error searching code: {e}"

@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find the definition of a function or a class."""
    # Matches `def my_func` or `class MyClass`
    pattern = rf"^\s*(def|class)\s+{name}\b"
    return search_code(pattern, "*.py")

@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    """Find all usages of a symbol (function or class)."""
    # Matches the exact symbol name anywhere
    pattern = rf"\b{name}\b"
    return search_code(pattern, "*.py")


@mcp.tool()
def run_command(command: str, workdir: str) -> str:
    """Execute a shell command in the specified working directory."""
    try:
        wd = resolve_safe_path(workdir)
        result = subprocess.run(
            command, shell=True, cwd=wd, capture_output=True, text=True
        )
        return (f"Exit Code: {result.returncode}\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}")
    except Exception as e:
        return f"Error executing command: {e}"

@mcp.tool()
def run_tests() -> str:
    """Execute the evaluation script."""
    try:
        testbed = get_testbed()
        # SWE-bench often places the eval script at the root, or passes it via the container setup
        eval_script = testbed / "eval.sh"
        cmd = "bash eval.sh" if eval_script.exists() else "pytest"
            
        result = subprocess.run(
            cmd, shell=True, cwd=testbed, capture_output=True, text=True
        )
        return (f"Exit Code: {result.returncode}\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}")
    except Exception as e:
        return f"Error running tests: {e}"

@mcp.tool()
def get_patch() -> str:
    """Retrieve the unified git diff of all changes made to the repository."""
    try:
        testbed = get_testbed()
        result = subprocess.run(
            ["git", "-c", "core.fileMode=false", "diff"],
            cwd=testbed, capture_output=True, text=True
        )
        if not result.stdout.strip():
            return "No changes found."
        return result.stdout
    except Exception as e:
        return f"Error retrieving patch: {e}"
