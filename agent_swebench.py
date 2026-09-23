import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import FrameType
from typing import Optional
import uuid

from dotenv import load_dotenv

from src.agent import AgentOrchestrator
from src.llm import TokenManager
from src.mcp_client import MCPClient
from src.models import SWEBenchTaskInput
from src.sandbox import Sandbox, SandboxConfig

load_dotenv()


def get_docker_host() -> str:
    """Return the Docker socket used by the current console context."""
    try:
        host = subprocess.check_output(
            [
                "docker",
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
            ],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if host:
            return host
    except subprocess.CalledProcessError:
        pass
    # Fall back to the standard socket
    return os.environ.get(
        "DOCKER_HOST", "unix:///var/run/docker.sock"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Smith SWE-bench Solver"
    )
    parser.add_argument(
        "--task-file", required=True, help="Path to the dumped task JSON file"
    )
    parser.add_argument(
        "--output", required=True, help="Path to save the SolutionOutput JSON"
    )
    parser.add_argument(
        "--model-name", required=True, help="LLM model identifier"
    )
    parser.add_argument(
        "--provider-url", required=True, help="Base URL for the LLM API"
    )
    args = parser.parse_args()

    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = SWEBenchTaskInput(**task_data)

    # Resolve the Docker socket used by the current console so that
    # subprocess calls target the same daemon as a manual `docker pull`.
    docker_env = os.environ.copy()
    docker_env["DOCKER_HOST"] = get_docker_host()

    # Pull image using the same Docker context as the console
    print(f"[*] Pulling Docker image: {task.docker_image}...")
    try:
        subprocess.run(
            ["docker", "pull", task.docker_image],
            check=True,
            env=docker_env,
        )
        print("[*] Docker pull completed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"[!] Warning: Failed to pull Docker image. Error: {e}")

    container_name = (
        f"swe_agent_{task.instance_id}_{uuid.uuid4().hex[:8]}"
    )
    print(
        f"[*] Starting Docker container: {container_name} "
        f"using {task.docker_image}"
    )

    # Signal handler for clean Docker container cleanup
    def signal_handler(
        sig: int, frame: Optional[FrameType]
    ) -> None:
        print(f"\n[*] Signal {sig} received.")
        sys.exit(128 + sig)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
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
            env=docker_env,  # same Docker daemon as the pull
        )

        server_env = docker_env.copy()
        server_env["SWE_CONTAINER_NAME"] = container_name
        server_env["TESTBED_PATH"] = "/testbed"
        server_env["SWEBENCH_TASK_FILE"] = str(
            Path(args.task_file).resolve()
        )

        mcp_client = MCPClient()
        mcp_tools_path = Path(__file__).parent / "mcp_tools_swebench.py"

        mcp_client.connect_stdio(f"python {mcp_tools_path}", env=server_env)

        # Create tool callables for sandbox injection
        mcp_tools_dict = mcp_client.make_tool_callables()

        sandbox_manual = mcp_client.get_sandbox_manual()
        config = SandboxConfig()
        sandbox = Sandbox(config=config, mcp_tools=mcp_tools_dict)

        try:
            token_manager = TokenManager(api_url=args.provider_url)
        except ValueError as e:
            print(f"Startup Error: {e}")
            return

        # Build hints section if available
        hints_section = ""
        if hasattr(task, "hints_text") and task.hints_text:
            hints_section = (
                f"\nHINTS (from the issue discussion):\n{task.hints_text}\n"
            )

        system_prompt = (
            "You are Agent Smith, an expert autonomous software engineer. "
            "You fix real bugs in real repositories.\n\n"
            "AVAILABLE TOOLS:\n"
            f"{sandbox_manual}\n\n"
            "Thought: <1-3 sentences of reasoning>\n"
            "Code:\n"
            "```python\n"
            "<exactly one tool call wrapped in print()>\n"
            "```\n\n"
            "METHODOLOGY:\n"
            "1. REPRODUCE: Run `print(run_tests())` to see failing tests\n"
            "2. LOCATE: Use search tools to find relevant code\n"
            "3. READ: Use `print(read_file(path, start, end))` to "
            "examine code\n"
            "4. PATCH: Use `print(edit_file(path, old_str, new_str))` "
            "to fix the bug\n"
            "5. VERIFY: Run `print(run_tests())` again to confirm the fix\n"
            "6. SUBMIT: Call `final_answer(get_patch())` immediately "
            "when tests pass\n\n"
            "CRITICAL RULES:\n"
            "- Output exactly ONE ```python block per response\n"
            "- ALWAYS wrap tool calls in print(): "
            "`print(read_file('/testbed/file.py', 1, 50))`\n"
            "- NEVER write standalone Python code — ONLY call tools\n"
            "- Keep Thought sections extremely concise (1-3 sentences)\n"
            "- For edit_file: copy old_str EXACTLY from read_file output "
            "(drop the 'N: ' prefix), preserving all whitespace\n"
            "- The test output is truncated at the BEGINNING. Look at the "
            "END for actual test results\n"
            "- The MOMENT tests pass, call `final_answer(get_patch())` "
            "— do NOT run tests again\n"
            "- Do NOT look up solutions from PRs, issues, or external "
            "sources\n"
        )

        task_prompt = (
            f"Fix the following issue for instance "
            f"{task.instance_id}:\n\n"
            f"Repository: {task.repo}\n\n"
            f"Problem Statement:\n{task.problem_statement}\n"
            f"{hints_section}\n"
            "Start by running the tests to see what's failing, then "
            "explore the codebase to understand and fix the bug."
        )

        orchestrator = AgentOrchestrator(
            sandbox, token_manager, args.model_name
        )
        solution_output = orchestrator.run(
            task_id=task.instance_id,
            benchmark="swebench",
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            max_iterations=30,
            max_input_tokens=300000,
            max_output_tokens=10000,
            max_time_seconds=840,
        )

        # Salvage patch if agent didn't submit one
        if not solution_output.success and not solution_output.solution:
            try:
                print("[*] Attempting to salvage partial patch...")
                patch = mcp_client.call_tool("get_patch", {})
                if (
                    patch
                    and patch.strip()
                    and "No changes made yet" not in patch
                ):
                    solution_output.solution = patch
            except Exception as e:
                print(f"[!] Failed to salvage patch: {e}")

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(solution_output.model_dump_json(indent=4))
        print(f"[*] Solution saved to {output_path}")

    finally:
        if "mcp_client" in locals():
            mcp_client.cleanup()


if __name__ == "__main__":
    main()