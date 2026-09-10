"""Agent Smith — main module entry point.

When run as `python -m src`, launches the interactive sandbox REPL.
For MBPP/SWE-bench agents, use:
  python -m agent_mbpp --task-file ... --output ...
  python -m agent_swebench --task-file ... --output ...
"""
from src.sandbox_cli import main

if __name__ == "__main__":
    main()
