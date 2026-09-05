import json
import argparse
import subprocess
import os
import uuid
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

    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = SWEBenchTaskInput(**task_data)

    container_name = f"swe_agent_{task.instance_id}_{uuid.uuid4().hex[:8]}"
    print(f"[*] Starting Docker container: {container_name} using {task.docker_image}")

    try:
        # Spin up the container and keep it alive in the background
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                task.docker_image,
                "tail",
                "-f",
                "/dev/null",
            ],
            check=True,
            capture_output=True,
        )

        # Pass the environment variables required by the tools
        os.environ["SWE_CONTAINER_NAME"] = container_name
        repo_name = task.instance_id.split('__')[0]
        os.environ["TESTBED_PATH"] = f"/{repo_name}"

        server_env = os.environ.copy()
        server_env["SWE_CONTAINER_NAME"] = container_name
        server_env["TESTBED_PATH"] = "/testbed" 

        mcp_client = MCPClient()
        mcp_tools_path = Path(__file__).parent / "mcp_tools_swebench.py"
        mcp_client.connect_stdio(f"python {mcp_tools_path}", env=server_env)

        # Create Sandbox Tool Dictionary
        # 3. Create Sandbox Tool Dictionary
        mcp_tools_dict = {}
        for tool in mcp_client.get_tools():
            tool_name = tool["name"]
            properties = tool.get("inputSchema", {}).get("properties", {})
            
            def make_tool_callable(name, props):
                def wrapper(*args, **kwargs):
                    call_args = {}
                    prop_keys = list(props.keys())
                    # Auto-map any positional arguments to their correct keyword names
                    for i, arg in enumerate(args):
                        if i < len(prop_keys):
                            call_args[prop_keys[i]] = arg
                    # Merge with any explicit keyword arguments
                    call_args.update(kwargs)
                    return mcp_client.call_tool(name, call_args)
                return wrapper
                
            mcp_tools_dict[tool_name] = make_tool_callable(tool_name, properties)
        # Sandbox & Orchestrator Setup
        sandbox_manual = mcp_client.get_sandbox_manual()
        config = SandboxConfig()
        sandbox = Sandbox(config=config, mcp_tools=mcp_tools_dict)
        token_manager = TokenManager()

        system_prompt = (
            "You are an autonomous software engineer. Your goal is to fix a bug in the provided codebase.\n"
            "You write Python code to interact with the system. Your code is executed in a sandbox.\n\n"
            "AVAILABLE TOOLS:\n"
            "- run_command(command: str): Runs a shell command. Returns stdout, stderr, and exit code.\n"
            "- read_file(filepath: str, start_line: int, end_line: int): Reads a specific line range from a file. Output includes line numbers.\n"
            "- edit_file(filepath: str, old_str: str, new_str: str): Replaces an exact block of text. `old_str` MUST match the file exactly, including all indentation and spaces. Copy it from `read_file` output (without the line numbers).\n"
            "- list_files(directory: str, pattern: str): Lists files matching a glob pattern (e.g., '*.py').\n"
            "- search_code(pattern: str, file_pattern: str = '*'): Searches for a regex pattern in files.\n"
            "- search_function_or_class_definition_in_code(name: str): Finds where a function/class is defined.\n"
            "- find_references(name: str, filepath: str, line: int): Finds where a symbol is used.\n"
            "- run_tests(): Runs the task's evaluation script to check if the issue is fixed.\n"
            "- get_patch(): Generates the git diff of your changes.\n\n"
            "METHODOLOGY (Follow Strictly):\n"
            "1. REPRODUCE: Run a minimal standalone reproduction script or use `run_tests()` to see the failing tests.\n"
            "2. LOCATE: Use the search tools to find the relevant files, classes, and functions.\n"
            "3. ANALYZE: Use `read_file` to read the specific line ranges surrounding the bug.\n"
            "4. PATCH: Use `edit_file` to apply your fix. Be extremely careful with `old_str` indentation.\n"
            "5. VERIFY: Run your reproduction script or `run_tests()` again to ensure the bug is resolved.\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. You MUST call the tools above as standard Python functions inside a ```python block.\n"
            "2. You MUST wrap your tool calls in a print() statement to see their output! (e.g., `print(run_tests())`).\n"
            "3. DO NOT output JSON tool calls. DO NOT import os or subprocess.\n"
            "4. ONE STEP AT A TIME: You MUST output exactly ONE ```python block per response. Stop writing immediately after your code block and wait for the system to return the execution results. Do NOT hallucinate or guess the output.\n"
            "5. STRICT LIMIT: Stop testing once you confirm the fix works.\n"
            "6. The MOMENT your verification passes, immediately call `final_answer(get_patch())` to submit."
        )

        task_prompt = (
            f"Fix the following issue for instance {task.instance_id}:\n\n"
            f"Problem Statement:\n{task.problem_statement}\n\n"
            "Explore the codebase, identify the bug, and use the get_patch() tool. "
            "Then, submit the patch using final_answer(patch_string)."
        )

        orchestrator = AgentOrchestrator(sandbox, token_manager, args.model_name)
        solution_output = orchestrator.run(
            task_id=task.instance_id,
            benchmark="swebench",
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            max_iterations=130,
            max_input_tokens=300000,
            max_output_tokens=10000,
            max_time_seconds=900
        )

        # Output Dump
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(solution_output.model_dump_json(indent=4))
        print(f"[*] Solution saved to {output_path}")

    finally:
        # Cleanup Guarantee
        print(f"[*] Cleaning up Docker container: {container_name}")
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        if "mcp_client" in locals():
            mcp_client.cleanup()


if __name__ == "__main__":
    main()
