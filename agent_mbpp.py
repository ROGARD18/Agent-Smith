import json
import argparse
from pathlib import Path
from dotenv import load_dotenv

from src.sandbox import Sandbox, SandboxConfig
from src.llm import TokenManager
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

    # Load task data
    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = MBPPTaskInput(**task_data)

    mcp_tools = {} 
    sandbox_manual = "You can write executable Python code to test your functions."
    
    config = SandboxConfig()
    sandbox = Sandbox(config=config, mcp_tools=mcp_tools)
    
    try:
        token_manager = TokenManager()
    except ValueError as e:
        print(f"Startup Error: {e}")
        return

    # MBPP-specific prompts
    system_prompt = (
        "You are an autonomous Python coding agent.\n"
        "Your goal is to write a Python function that solves the provided problem and passes all given tests.\n"
        "You can test your code in the sandbox by writing Python code.\n"
        "When you are confident your function is correct, you MUST submit the FULL function definition "
        "using `final_answer(solution_code_string)`. Ensure the submitted solution contains all necessary imports."
    )

    tests_str = "\n".join(task.test_list)
    imports_str = "\n".join(task.test_imports)
    
    task_prompt = (
        f"Problem Statement:\n{task.task_definition}\n\n"
        f"Your function must pass the following tests:\n```python\n{imports_str}\n{tests_str}\n```\n\n"
        "Write the code, test it with the asserts, and submit the final function as a string via final_answer()."
    )

    orchestrator = AgentOrchestrator(sandbox, token_manager, args.model_name)
    
    solution_output = orchestrator.run(
                task_id=str(task.task_id),
                benchmark="mbpp",
                system_prompt=system_prompt,
                task_prompt=task_prompt,
                max_iterations=10,
                max_input_tokens=60000,
                max_output_tokens=1500,
                max_time_seconds=120
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(solution_output.model_dump_json(indent=4))
        
    print(f"[*] MBPP Solution saved to {output_path}")

if __name__ == "__main__":
    main()