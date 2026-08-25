import os
import requests
from dotenv import load_dotenv
import re

load_dotenv()

api_key = os.getenv("OPENROUTER_API_KEY")

answer = requests.post(
    url="https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}"},
    json={
        "model": "nvidia/nemotron-3.5-lightning:free",
        "messages": [{"role": "user",
                      "content": "Peux tu ecrire une fonction python qui"
                      "addtione deux variable int passes en parametre. Le code"
                      "doit etre encadre avec '''python et ''' comme en markdown."}]
    }
)

texte = answer.json()['choices'][0]['message']['content']

match = re.search(r"```python\n(.*?)\n```", texte, re.DOTALL)

if match:
    code_extrait = match.group(1)
    print("Code extrait :\n", code_extrait)
else:
    print("Aucun code Python trouvé dans la réponse.")

