import argparse
import sys
import os
import code

# Add repo root to sys.path to import local modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sandbox import Sandbox, SandboxConfig  # noqa: E402
from src.mcp_client import MCPClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Sandbox REPL")
    parser.add_argument("config_file", nargs="?",
                        help="Path to sandbox config JSON file")
    parser.add_argument("--mcp-stdio",
                        help="Command to start MCP stdio server")
    parser.add_argument("--mcp-server",
                        help="URL of MCP HTTP server")
    args = parser.parse_args()

    config = SandboxConfig()
    if args.config_file:
        try:
            config = SandboxConfig.from_json_file(args.config_file)
        except Exception as e:
            print(f"Failed to load config: {e}")
            sys.exit(1)
    elif os.path.exists("sandbox_template.json"):
        try:
            config = SandboxConfig.from_json_file("sandbox_template.json")
        except Exception:
            pass
    elif os.path.exists("sandbox_config.json"):
        try:
            config = SandboxConfig.from_json_file("sandbox_config.json")
        except Exception:
            pass

    mcp_client = None
    mcp_tools = {}
    if args.mcp_stdio or args.mcp_server:
        mcp_client = MCPClient()
        if args.mcp_stdio:
            server_env = os.environ.copy()
            mcp_client.connect_stdio(args.mcp_stdio, env=server_env)
        elif args.mcp_server:
            mcp_client.connect_streamable_http(args.mcp_server)

        try:
            mcp_tools = mcp_client.make_tool_callables()
            print(f"[*] Loaded {len(mcp_tools)} MCP tool(s): "
                  f"{', '.join(mcp_tools.keys())}")
        except Exception as e:
            print(f"Failed to fetch MCP tools: {e}")

    sandbox = Sandbox(config=config, mcp_tools=mcp_tools)

    print("Sandbox Interactive REPL")
    print("Type 'exit' or use Ctrl+D to quit.")

    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    buffer: list[str] = []

    while True:
        try:
            prompt = "... " if buffer else ">>> "
            line = input(prompt)

            if line.strip() == "exit" and not buffer:
                break

            buffer.append(line)
            source = "\n".join(buffer)

            try:
                # compile_command returns None if the command is incomplete
                compiled = code.compile_command(source, symbol="exec")
                if compiled is None:
                    continue
            except (SyntaxError, OverflowError, ValueError):
                pass

            result = sandbox.execute(source)
            status = result.get('status')
            data = result.get('data')

            if status == 'error':
                print(f"Error: {data}")
            elif status == 'final_answer':
                print(f"Final Answer: {data}")
                break
            else:
                if data is not None and str(data).strip() != "":
                    print(f"{data}")

            buffer = []

        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt")
            buffer = []
            continue

    if mcp_client:
        mcp_client.cleanup()


if __name__ == "__main__":
    main()
