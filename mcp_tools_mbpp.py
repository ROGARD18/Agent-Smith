import ast
import argparse
import subprocess
import tempfile
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MBPP-Tools")

@mcp.tool()
def check_syntax(code: str) -> str:
    """
    Checks the Python code for syntax errors without executing it.
    Use this to verify your function before running it in the sandbox.
    """
    try:
        ast.parse(code)
        return "Syntax is valid."
    except SyntaxError as e:
        return f"SyntaxError on line {e.lineno}, offset {e.offset}: {e.msg}\nCode context: {e.text}"
    except Exception as e:
        return f"Error parsing code: {e}"

@mcp.tool()
def run_tests(code: str, test_list: list) -> str:
    """
    Executes a candidate solution against a list of test assertions.
    Returns a JSON string containing {"success": bool, "output": str}.
    """
    full_script = f"{code}\n\n"
    for test in test_list:
        full_script += f"{test}\n"
    
    try:
        # Exécution dans un sous-processus temporaire pour isoler les assertions
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tmp:
            tmp.write(full_script)
            tmp_path = tmp.name

        result = subprocess.run(
            ["python", tmp_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        success = (result.returncode == 0)
        output = result.stdout
        if result.stderr:
            output += f"\nErrors:\n{result.stderr}"
            
        return json.dumps({
            "success": success,
            "output": output.strip() if output.strip() else "All tests passed silently."
        })
        
    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "output": "Execution timed out."})
    except Exception as e:
        return json.dumps({"success": False, "output": f"Execution error: {str(e)}"})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MBPP MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", 
                        help="Transport protocol to use (stdio or sse)")
    parser.add_argument("--port", type=int, default=8001, 
                        help="Port for SSE server (if transport=sse)")
    
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", port=args.port)