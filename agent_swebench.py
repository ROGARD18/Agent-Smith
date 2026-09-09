import json
import argparse
import subprocess
import os
import uuid
import signal
import sys
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
    parser.add_argument("--task-file", required=True, help="Path to the dumped task JSON file")
    parser.add_argument("--output", required=True, help="Path to save the SolutionOutput JSON")
    parser.add_argument("--model-name", required=True, help="LLM model identifier")
    parser.add_argument("--provider-url", required=True, help="Base URL for the LLM API")
    args = parser.parse_args()

    with open(args.task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
    task = SWEBenchTaskInput(**task_data)

    container_name = f"swe_agent_{task.instance_id}_{uuid.uuid4().hex[:8]}"
    print(f"[*] Starting Docker container: {container_name} using {task.docker_image}")

    # Gestion propre du SIGTERM pour garantir le nettoyage
    def signal_handler(sig, frame):
        print(f"\n[*] SIGTERM received. Cleaning up Docker container: {container_name}")
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        sys.exit(128 + sig)
        
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        subprocess.run(
            ["docker", "run", "-d", "--name", container_name, task.docker_image, "tail", "-f", "/dev/null"],
            check=True,
            capture_output=True,
        )

        server_env = os.environ.copy()
        server_env["SWE_CONTAINER_NAME"] = container_name
        server_env["TESTBED_PATH"] = "/testbed" 
        # Crucial : Fournit le fichier de tâche pour que run_tests() charge le script d'évaluation
        server_env["SWEBENCH_TASK_FILE"] = str(Path(args.task_file).resolve())

        mcp_client = MCPClient()
        mcp_tools_path = Path(__file__).parent / "mcp_tools_swebench.py"
        
        # CORRECTION 1 : Ajout des guillemets autour du chemin au cas où il y a des espaces
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

        sandbox_manual = mcp_client.get_sandbox_manual()
        config = SandboxConfig()
        sandbox = Sandbox(config=config, mcp_tools=mcp_tools_dict)
        
        try:
            token_manager = TokenManager()
        except ValueError as e:
            print(f"Startup Error: {e}")
            return

        # CORRECTION 2 : Ajout vital de l'exemple de formatage (EXAMPLE FORMAT) 
        # Si le LLM ne voit pas les backticks de fermeture dans le prompt, il ne les écrira jamais !
        system_prompt = (
            "You are an autonomous software engineer. Your goal is to fix a bug in the provided codebase.\n\n"
            "AVAILABLE TOOLS:\n"
            f"{sandbox_manual}\n\n"
            "METHODOLOGY (Follow Strictly):\n"
            "1. REPRODUCE: Run `run_tests()` to see the failing tests.\n"
            "2. LOCATE: Use the search tools to find the relevant files.\n"
            "3. ANALYZE: Use `read_file` to read the specific line ranges.\n"
            "4. PATCH: Use `edit_file` to apply your fix. Be extremely careful with indentation.\n"
            "5. VERIFY: Run `run_tests()` again to ensure the bug is resolved.\n\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. You MUST call the tools above inside a ```python block.\n"
            "2. You MUST wrap your tool calls in a print() statement.\n"
            "3. ONE STEP AT A TIME: Output exactly ONE ```python block per response.\n"
            "4. The MOMENT your verification passes, call `final_answer(get_patch())`.\n\n"
            "EXAMPLE FORMAT:\n"
            "```python\n"
            "print(run_tests())\n"
            "```\n"
        )

        task_prompt = (
            f"Fix the following issue for instance {task.instance_id}:\n\n"
            f"Problem Statement:\n{task.problem_statement}\n\n"
            "Explore the codebase, identify the bug, and use the get_patch() tool."
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
            max_time_seconds=840 # Tolérance de 60s pour éviter le kill brutal
        )

        # CORRECTION 3 : Sécurisation de l'appel de secours avec un try/except
        if not solution_output.success and not solution_output.solution:
            try:
                print("[*] Attempting to salvage partial patch...")
                solution_output.solution = mcp_client.call_tool("get_patch", {})
            except Exception as e:
                print(f"[!] Failed to salvage patch: {e}")

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(solution_output.model_dump_json(indent=4))
        print(f"[*] Solution saved to {output_path}")

    finally:
        print(f"[*] Cleaning up Docker container: {container_name}")
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        if "mcp_client" in locals():
            mcp_client.cleanup()

if __name__ == "__main__":
    main()