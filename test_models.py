import os, requests

key = os.environ.get("GEMINI_API_KEY")
r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
for m in r.json().get("models", []):
    print(m["name"])
