import re
import shlex

file_path = (
    "moulinette/.venv/lib/python3.14/site-packages/swebench/harness/docker_utils.py"
)
with open(file_path, "r") as f:
    content = f.read()


def repl(match):
    return """def write_to_container(container: Container, data: str, dst: Path):
    import shlex
    command = f"cat <<'EOF_1399519320' > {dst}\\n{data}\\nEOF_1399519320"
    container.exec_run(["/bin/bash", "-c", command])"""


content = re.sub(
    r"def write_to_container.*?container\.exec_run\(command\)",
    repl,
    content,
    flags=re.DOTALL,
)

with open(file_path, "w") as f:
    f.write(content)
