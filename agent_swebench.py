import json
import argparse
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv


from src.sandbox import Sandbox, SandboxConfig
from src.llm import TokenManager
from agent import AgentOrchestrator
from src.models import SWEBenchTaskInput


load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Agent Smith SWE-bench Solver")
    
    parser.add_argument("--task-file", required=True, help="Path to the dumped task JSON file")
    parser.add_argument("--output", required=True, help="Path to save the SolutionOutput JSON")
    parser.add_argument("--model-name", required=True, help="LLM model identifier")
    parser.add_argument("--provider-url", required=True, help="Base URL for the LLM API")
    
    args = parser.parse_args()

    # Load the task data
    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = SWEBenchTaskInput(**task_data)

    # Setup Sandbox and Tools (Placeholder for MCP connection)
    mcp_tools = {} 
    sandbox_manual = "Available Tools: read_file, edit_file, search_code, get_patch, run_tests..." 
    
    config = SandboxConfig()
    sandbox = Sandbox(config=config, mcp_tools=mcp_tools)
    
    try:
        token_manager = TokenManager()
    except ValueError as e:
        print(f"Startup Error: {e}")
        return

    system_prompt = (
        "You are an autonomous coding agent. Your goal is to fix bugs in a codebase.\n"
        "You write Python code to interact with the system. Your code is executed in a sandbox.\n"
        f"SANDBOX MANUAL:\n{sandbox_manual}\n\n"
        "To finish, you must call `final_answer(get_patch())` with the generated git patch."
    )

    # SWE specific prompt
    task_prompt = (
        f"Fix the following issue in the {task.repo} repository:\n\n"
        f"Problem Statement:\n{task.problem_statement}\n\n"
        f"Hints:\n{task.hints_text}\n\n"
        "Explore the codebase, identify the bug, and use the get_patch() tool. "
        "Then, submit the patch using final_answer(patch_string)."
    )

    # Initialize and run your existing orchestrator
    orchestrator = AgentOrchestrator(sandbox, token_manager, args.model_name)
    
    # The SWE-bench benchmark limits execution to 30 iterations
    solution_output = orchestrator.run(
        task_id=task.instance_id,
        benchmark="swebench",
        system_prompt=system_prompt,
        task_prompt=task_prompt,
        max_iterations=30
    )

    # Dump the output to the requested file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=4))
        
    print(f"[*] Solution saved to {output_path}")

if __name__ == "__main__":
    main()