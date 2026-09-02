import ast
import argparse
from mcp.server.fastmcp import FastMCP

# Initialize the FastMCP server for MBPP
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MBPP MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", 
                        help="Transport protocol to use (stdio or sse)")
    parser.add_argument("--port", type=int, default=8001, 
                        help="Port for SSE server (if transport=sse)")
    
    args = parser.parse_args()

    # Supports both stdio and http as required by the subject
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", port=args.port)