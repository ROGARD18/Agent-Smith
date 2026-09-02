import json
import argparse
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv


from src.sandbox import Sandbox, SandboxConfig
from src.llm import TokenManager
from src.mcp_client import MCPClient
from src.agent import AgentOrchestrator
from src.models import SWEBenchTaskInput

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Agent Smith SWE-bench Solver")

    parser.add_argument(
        "--task-file", required=True, help="Path to the dumped task JSON file"
    )
    parser.add_argument(
        "--output", required=True, help="Path to save the SolutionOutput JSON"
    )
    parser.add_argument("--model-name", required=True, help="LLM model identifier")
    parser.add_argument(
        "--provider-url", required=True, help="Base URL for the LLM API"
    )

    args = parser.parse_args()

    # Load task data
    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = SWEBenchTaskInput(**task_data)

    mcp_client = MCPClient()

    # Path to the mandatory SWE-bench MCP server
    mcp_tools_path = Path(__file__).parent / "mcp_tools_swebench.py"
    try:
        mcp_client.connect_stdio(f"python {mcp_tools_path}")
    except Exception as e:
        print(f"Failed to connect to MCP server: {e}")
        return

    # Dictionary of callable functions for the Sandbox
    mcp_tools_dict = {}
    for tool in mcp_client.get_tools():
        tool_name = tool["name"]

        # Avoid Python's late-binding closure issue in loops
        def make_tool_callable(name):
            return lambda **kwargs: mcp_client.call_tool(name, kwargs)

        mcp_tools_dict[tool_name] = make_tool_callable(tool_name)

    # Fetch the dynamically generated manual based on the server's tools
    sandbox_manual = mcp_client.get_sandbox_manual()

    config = SandboxConfig()
    sandbox = Sandbox(config=config, mcp_tools=mcp_tools_dict)

    try:
        token_manager = TokenManager()
    except ValueError as e:
        print(f"Startup Error: {e}")
        return

    # 5. Define SWE-bench specific prompts
    system_prompt = (
        "You are an autonomous coding agent. Your goal is to fix bugs in a codebase.\n"
        "You write Python code to interact with the system. Your code is executed in a sandbox.\n\n"
        f"SANDBOX MANUAL:\n{sandbox_manual}\n\n"
        "To finish, you must call `final_answer(get_patch())` with the generated git patch."
    )

    task_prompt = (
        f"Fix the following issue in the {task.repo} repository:\n\n"
        f"Problem Statement:\n{task.problem_statement}\n\n"
        f"Hints:\n{task.hints_text}\n\n"
        "Explore the codebase, identify the bug, and use the get_patch() tool. "
        "Then, submit the patch using final_answer(patch_string)."
    )

    orchestrator = AgentOrchestrator(
        sandbox=sandbox, token_manager=token_manager, model_name=args.model_name
    )

    solution_output = orchestrator.run(
        task_id=task.instance_id,
        benchmark="swebench",
        system_prompt=system_prompt,
        task_prompt=task_prompt,
        max_iterations=30,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=4))

    print(f"[*] Solution saved to {output_path}")

    mcp_client.cleanup()


if __name__ == "__main__":
    main()
