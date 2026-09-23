import asyncio
from typing import Dict, Any, List, Optional, Callable
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client


class MCPClient:
    """Manages connections to MCP tool servers via stdio or HTTP/SSE.

    Supports both stdio (local subprocess) and streamable HTTP transports.
    Dynamically discovers tools from the connected server and generates
    documentation for the LLM prompt.
    """

    def __init__(self) -> None:
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()
        self._loop = asyncio.new_event_loop()
        self._tools_cache: List[Dict[str, Any]] = []

    def connect_stdio(self, command: str,
                      env: Optional[Dict[str, str]] = None
                      ) -> None:
        """Connects to an MCP server running as a local subprocess."""
        cmd_parts = command.split()
        server_params = StdioServerParameters(
            command=cmd_parts[0], args=cmd_parts[1:], env=env
        )
        self._loop.run_until_complete(self._init_stdio(server_params))
        self._loop.run_until_complete(self._fetch_tools())

    def connect_http(self, url: str) -> None:
        """Connects to an MCP server via HTTP/SSE (streamable HTTP)."""
        self._loop.run_until_complete(self._init_http(url))
        self._loop.run_until_complete(self._fetch_tools())

    def connect_streamable_http(self, url: str) -> None:
        """Connects to an MCP server via streamable HTTP transport.

        This is an alias for connect_http that tries the streamable HTTP
        transport first, falling back to SSE if not supported.
        """
        try:
            self._loop.run_until_complete(self._init_streamable_http(url))
            self._loop.run_until_complete(self._fetch_tools())
        except Exception:
            # Fallback to SSE transport
            print("[*] Streamable HTTP failed, falling back to SSE...")
            self._loop.run_until_complete(self._init_http(url))
            self._loop.run_until_complete(self._fetch_tools())

    async def _init_stdio(self, server_params: StdioServerParameters) -> None:
        transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(transport[0], transport[1])
        )
        assert self.session is not None
        await self.session.initialize()

    async def _init_http(self, url: str) -> None:
        transport = await self._exit_stack.enter_async_context(sse_client(url))
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(transport[0], transport[1])
        )
        assert self.session is not None
        await self.session.initialize()

    async def _init_streamable_http(self, url: str) -> None:
        """Try streamable HTTP transport (mcp library >= 1.x)."""
        try:
            from mcp.client.streamable_http import streamablehttp_client

            transport = await self._exit_stack.enter_async_context(
                streamablehttp_client(url)
            )
            self.session = await self._exit_stack.enter_async_context(
                ClientSession(transport[0], transport[1])
            )
            assert self.session is not None
            await self.session.initialize()
        except ImportError:
            # Library doesn't have streamable HTTP yet, use SSE
            await self._init_http(url)

    async def _fetch_tools(self) -> None:
        """Retrieves the list of tools available on the server."""
        if not self.session:
            raise RuntimeError("MCP session not initialized.")

        response = await self.session.list_tools()
        self._tools_cache = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in response.tools
        ]

    def get_tools(self) -> List[Dict[str, Any]]:
        """Returns the cached tool definitions."""
        return self._tools_cache

    def get_sandbox_manual(self) -> str:
        """Dynamically generates the sandbox manual from connected MCP tools.

        This is what the LLM reads to understand what tools are available
        and how to call them. When a different MCP server is connected,
        the manual automatically reflects that server's tools.
        """
        if not self._tools_cache:
            return "No external tools available."

        manual = "AVAILABLE PYTHON FUNCTIONS:\n"
        manual += (
            "Call these functions directly in your Python code. "
            "They are available in the sandbox namespace.\n\n"
        )

        for tool in self._tools_cache:
            name = tool["name"]
            desc = tool.get("description", "No description available")
            schema = tool.get("inputSchema", {})
            props = schema.get("properties", {})
            required = set(schema.get("required", []))

            # Build function signature with types
            params = []
            for param_name, param_info in props.items():
                param_type = param_info.get("type", "any")
                is_required = param_name in required
                default = param_info.get("default")

                if is_required or default is None:
                    params.append(f"{param_name}: {param_type}")
                else:
                    params.append(f"{param_name}: {param_type}"
                                  f" = {repr(default)}")

            args_str = ", ".join(params)

            manual += f"- {name}({args_str}) -> str\n"
            manual += f"  {desc}\n"

            # Add parameter descriptions if available
            for param_name, param_info in props.items():
                param_desc = param_info.get("description", "")
                if param_desc:
                    manual += f"    {param_name}: {param_desc}\n"

            manual += "\n"

        manual += (
            "Additionally, `final_answer(solution)` is always available "
            "to submit your final answer.\n"
        )

        return manual

    def make_tool_callables(self) -> Dict[str, Callable[..., Any]]:
        """Create callable wrappers for all MCP tools.

        Returns a dict of {tool_name: callable} that can be passed
        to the Sandbox as mcp_tools.
        """
        tools_dict: Dict[str, Callable[..., Any]] = {}
        for tool in self._tools_cache:
            tool_name = str(tool["name"])
            properties = tool.get("inputSchema", {}).get("properties", {})

            def make_tool_callable(
                name: str, props: Dict[str, Any]
            ) -> Callable[..., Any]:
                def wrapper(*args: Any, **kwargs: Any) -> str:
                    call_args: Dict[str, Any] = {}
                    prop_keys = list(props.keys())
                    for i, arg in enumerate(args):
                        if i < len(prop_keys):
                            call_args[prop_keys[i]] = arg
                    call_args.update(kwargs)
                    return self.call_tool(name, call_args)

                return wrapper

            tools_dict[tool_name] = make_tool_callable(tool_name, properties)

        return tools_dict

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Synchronously calls a tool on the remote server."""
        if not self.session:
            raise RuntimeError("MCP session not initialized.")
        result = self._loop.run_until_complete(
            self._call_tool_async(name, arguments)
            )
        return str(result)

    async def _call_tool_async(self, name: str,
                               arguments: Dict[str, Any]
                               ) -> str:
        if not self.session:
            return "Error: MCP session not initialized."
        try:
            result = await self.session.call_tool(name, arguments)
            # Extract the text from MCP
            return "\n".join(
                content.text for content in result.content
                if content.type == "text"
            )
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"

    def cleanup(self) -> None:
        """Closes connections and cleans up the event loop."""
        try:
            if not self._loop.is_closed():
                self._loop.run_until_complete(self._exit_stack.aclose())
                self._loop.close()
        except Exception:
            pass
