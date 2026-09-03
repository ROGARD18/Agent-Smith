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
            "You are an autonomous coding agent. Your goal is to fix bugs in a codebase.\n"
            "You write Python code to interact with the system. Your code is executed in a sandbox.\n\n"
            f"SANDBOX MANUAL:\n{sandbox_manual}\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. You MUST call the tools above as standard Python functions inside a ```python block.\n"
            "2. You MUST wrap your tool calls in a print() statement to see their output! (e.g., `print(run_command('ls -la'))`).\n"
            "3. DO NOT output JSON tool calls. DO NOT import os or subprocess.\n"
            "4. STRICT LIMIT: You only have 30 iterations. Do NOT over-verify. Once your manual reproduction script confirms the fix, stop testing.\n"
            "5. The MOMENT you confirm the fix works, you MUST call `final_answer(get_patch())` to submit."
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
            max_iterations=30,
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
