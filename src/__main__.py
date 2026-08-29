from src.llm import generate_response


prompt: str = ""


def main():

    response = generate_response(prompt)


if __name__ == "__main__":
    main()
