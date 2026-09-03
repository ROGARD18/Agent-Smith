import asyncio
import json
from typing import Dict, Any, List, Optional
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client

class MCPClient:
    """Manages connections to MCP tool servers via stdio or HTTP/SSE."""
    
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()
        self._loop = asyncio.new_event_loop()
        self._tools_cache: List[Dict] = []

    def connect_stdio(self, command: str, env: dict = None):
        """Connects to an MCP server running as a local subprocess."""
        cmd_parts = command.split()
        server_params = StdioServerParameters(
            command=cmd_parts[0], 
            args=cmd_parts[1:],
            env=env
        )
        self._loop.run_until_complete(self._init_stdio(server_params))
        self._loop.run_until_complete(self._fetch_tools())

    def connect_http(self, url: str):
        """Connects to an MCP server via HTTP/SSE."""
        self._loop.run_until_complete(self._init_http(url))
        self._loop.run_until_complete(self._fetch_tools())

    async def _init_stdio(self, server_params: StdioServerParameters):
        transport = await self._exit_stack.enter_async_context(stdio_client(server_params))
        self.session = await self._exit_stack.enter_async_context(ClientSession(transport[0], transport[1]))
        await self.session.initialize()

    async def _init_http(self, url: str):
        transport = await self._exit_stack.enter_async_context(sse_client(url))
        self.session = await self._exit_stack.enter_async_context(ClientSession(transport[0], transport[1]))
        await self.session.initialize()

    async def _fetch_tools(self):
        """Retrieves the list of tools available on the server."""
        if not self.session:
            raise RuntimeError("MCP session not initialized.")
        
        response = await self.session.list_tools()
        self._tools_cache = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema
            }
            for tool in response.tools
        ]

    def get_tools(self) -> List[Dict]:
        """Returns the cached tool definitions."""
        return self._tools_cache

    def get_sandbox_manual(self) -> str:
        """Dynamically generates the prompt manual based on available tools."""
        if not self._tools_cache:
            return "No external tools available."
            
        manual = "AVAILABLE TOOLS:\n"
        for tool in self._tools_cache:
            manual += f"- {tool['name']}: {tool['description']}\n"
            schema_str = json.dumps(tool['inputSchema']['properties'])
            manual += f"  Arguments: {schema_str}\n\n"
        return manual

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Synchronously calls a tool on the remote server."""
        if not self.session:
            raise RuntimeError("MCP session not initialized.")
        return self._loop.run_until_complete(self._call_tool_async(name, arguments))

    async def _call_tool_async(self, name: str, arguments: Dict[str, Any]) -> str:
        try:
            result = await self.session.call_tool(name, arguments)
            # Extract the text from MCP
            return "\n".join(content.text for content in result.content if content.type == "text")
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"

    def cleanup(self):
        """Closes connections and cleans up the event loop."""
        try:
            if not self._loop.is_closed():
                self._loop.run_until_complete(self._exit_stack.aclose())
                self._loop.close()
        except Exception:
            pass