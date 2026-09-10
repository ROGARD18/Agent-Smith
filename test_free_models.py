import requests
import os
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("OPENROUTER_API_KEY")
headers = {"Authorization": f"Bearer {key}"}
resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers)
if resp.status_code == 200:
    models = resp.json().get("data", [])
    free_models = [m["id"] for m in models if m["id"].endswith(":free")]
    print(f"Found {len(free_models)} free models:")
    for fm in free_models:
        print(f" - {fm}")
else:
    print("Failed to fetch models:", resp.status_code, resp.text)
