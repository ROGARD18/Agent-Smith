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
    
    parser.add_argument("--task-file", required=True, help="Path to the dumped task JSON file")
    parser.add_argument("--output", required=True, help="Path to save the SolutionOutput JSON")
    parser.add_argument("--model-name", required=True, help="LLM model identifier")
    parser.add_argument("--provider-url", required=True, help="Base URL for the LLM API")
    
    args = parser.parse_args()

    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = MBPPTaskInput(**task_data)

    # Intégration du client MCP pour MBPP
    mcp_client = MCPClient()
    mcp_tools_path = Path(__file__).parent / "mcp_tools_mbpp.py"
    server_env = os.environ.copy()
    mcp_client.connect_stdio(f"python {mcp_tools_path}", env=server_env)

    mcp_tools_dict = {}
    for tool in mcp_client.get_tools():
        tool_name = tool["name"]
        properties = tool.get("inputSchema", {}).get("properties", {})
        
        def make_tool_callable(name, props):
            def wrapper(*args, **kwargs):
                call_args = {}
                prop_keys = list(props.keys())
                for i, arg in enumerate(args):
                    if i < len(prop_keys):
                        call_args[prop_keys[i]] = arg
                call_args.update(kwargs)
                return mcp_client.call_tool(name, call_args)
            return wrapper
            
        mcp_tools_dict[tool_name] = make_tool_callable(tool_name, properties)
    
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
        "Your goal is to write a Python function that solves the provided problem and passes all given tests.\n"
        f"{sandbox_manual}\n"
        "When you are confident your function is correct, you MUST submit the FULL function definition "
        "using `final_answer(solution_code_string)`. Ensure the submitted solution contains all necessary imports."
    )

    tests_str = "\n".join(task.test_list)
    imports_str = "\n".join(task.test_imports)
    
    task_prompt = (
        f"Problem Statement:\n{task.task_definition}\n\n"
        f"Your function must pass the following tests:\n```python\n{imports_str}\n{tests_str}\n```\n\n"
        "Write the code, verify it with `run_tests(code, test_list)` and submit the final function as a string via final_answer()."
    )

    orchestrator = AgentOrchestrator(sandbox, token_manager, args.model_name)
    
    solution_output = orchestrator.run(
                task_id=str(task.task_id),
                benchmark="mbpp",
                system_prompt=system_prompt,
                task_prompt=task_prompt,
                max_iterations=10,
                max_input_tokens=6000,  # Limite stricte MBPP corrigée
                max_output_tokens=1500, # Limite stricte MBPP corrigée
                max_time_seconds=100    # Marge de sécurité par rapport aux 120s
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=4))
        
    print(f"[*] MBPP Solution saved to {output_path}")
    mcp_client.cleanup()

if __name__ == "__main__":
    main()