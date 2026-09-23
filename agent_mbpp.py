import json
import argparse
import os
from pathlib import Path
from dotenv import load_dotenv

from src.sandbox import Sandbox, SandboxConfig
from src.llm import TokenManager
from src.mcp_client import MCPClient
from src.agent import AgentOrchestrator
from src.models import MBPPTaskInput

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Agent Smith MBPP Solver")

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
    task = MBPPTaskInput(**task_data)

    # Connect to MBPP MCP tools server
    mcp_client = MCPClient()
    mcp_tools_path = Path(__file__).parent / "mcp_tools_mbpp.py"
    server_env = os.environ.copy()
    mcp_client.connect_stdio(f"python {mcp_tools_path}", env=server_env)

    # Create tool callables for sandbox injection
    mcp_tools_dict = mcp_client.make_tool_callables()

    config = SandboxConfig()
    sandbox = Sandbox(config=config, mcp_tools=mcp_tools_dict)

    try:
        token_manager = TokenManager(api_url=args.provider_url)
    except ValueError as e:
        print(f"Startup Error: {e}")
        return

    sandbox_manual = mcp_client.get_sandbox_manual()

    system_prompt = (
        "You are an autonomous Python coding agent.\n"
        "Your goal is to write a Python function that solves the provided "
        "problem and passes all given tests.\n\n"
        "TOOLS:\n"
        f"{sandbox_manual}\n\n"
        "WORKFLOW:\n"
        "1. Read the problem and tests carefully\n"
        "2. Write your solution function\n"
        "3. Test it with run_tests(code, test_list)\n"
        "4. If tests pass, submit via final_answer(code_string)\n\n"
        'RESPONSE FORMAT (follow EXACTLY every time):\nExample Response:\nThought: I will write the function and test it.\nCode:\n```python\nmy_code = "def add(a, b): return a + b"\nprint(run_tests(my_code, ["assert add(1, 2) == 3"]))\n```\n\n'
        "Thought: <brief reasoning about your approach>\n"
        "Code:\n"
        "```python\n"
        "<your executable Python code>\n"
        "```\n\n"
        "RULES:\n"
        "- You MUST output exactly ONE ```python block per response\n"
        "- Always wrap tool calls in print(): print(run_tests(code, tests))\n"
        "- final_answer() takes a STRING of your function code, not the "
        "function itself\n"
        "- Include all necessary imports in the submitted code string\n"
        "- Be concise — you have limited iterations\n\n"
        "EXAMPLE:\n"
        "Thought: I'll write the function and test it.\n"
        "Code:\n"
        "```python\n"
        "code = '''def square(x):\n"
        "    return x * x'''\n"
        "tests = ['assert square(2) == 4', 'assert square(3) == 9']\n"
        "print(run_tests(code, tests))\n"
        "```\n\n"
        "After tests pass:\n"
        "Thought: Tests passed. Submitting the solution.\n"
        "Code:\n"
        "```python\n"
        "final_answer('''def square(x):\n"
        "    return x * x''')\n"
        "```\n"
    )

    tests_str = "\n".join(task.test_list)
    imports_str = "\n".join(task.test_imports) if task.test_imports else ""

    task_prompt = (
        f"Problem Statement:\n{task.task_definition}\n\n"
        f"Function signature: {task.function_definition}\n\n"
    )
    if imports_str:
        task_prompt += f"Required imports for tests:\n{imports_str}\n\n"
    task_prompt += (
        f"Your function must pass these tests:\n"
        f"```python\n{tests_str}\n```\n\n"
        f"Write the function, test it with run_tests(code, test_list), "
        f"and submit via final_answer(code_string)."
    )

    orchestrator = AgentOrchestrator(sandbox, token_manager, args.model_name)

    solution_output = orchestrator.run(
        task_id=str(task.task_id),
        benchmark="mbpp",
        system_prompt=system_prompt,
        task_prompt=task_prompt,
        max_iterations=10,
        max_input_tokens=6000,
        max_output_tokens=1500,
        max_time_seconds=100,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=4))

    print(f"[*] MBPP Solution saved to {output_path}")
    mcp_client.cleanup()


if __name__ == "__main__":
    main()
