from src.llm import generate_response
from src.sandbox import Sandbox, SandboxConfig


prompt: str = (
    "Write a Python function that takes a list of integers "
    "and returns the sum of all even numbers. Please enclose "
    "the code strictly within python and  blocks."
)


def main():

    code = generate_response(prompt)
    



if __name__ == "__main__":
    main()
