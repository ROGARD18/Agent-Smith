# Agent Smith — Diagnostic

Analyse du code (`clone/`) au regard du sujet (`agent_subject.pdf`, v1.1) et des traces
réelles présentes dans `clone/cache/*_solution.json`.

Trois symptômes rapportés : **trop de tokens**, **rate limit dès le 1er appel**,
**pas de convergence en 30 itérations**. Ils ont des causes distinctes, toutes
identifiables dans le code.

---

## 0. Rappel des limites du sujet (§6.1)

| Métrique | MBPP | SWE-bench |
|---|---|---|
| Itérations max | **10** | **30** |
| Input tokens (cumulés) | **6 000** | 300 000 |
| Output tokens (cumulés) | 1 500 | **10 000** |
| Timeout | 120 s | 900 s |

Les tokens sont **cumulés sur toutes les itérations** d'une même tâche. Les tokens de
*reasoning* comptent comme les autres. Le timeout est appliqué par **SIGTERM puis
SIGKILL** sur le process de l'agent, pas vérifié après coup.

Seuils de réussite : MBPP 4/5 tâches, SWE-bench 2/3 tâches. Aucun retry autorisé.

---

## 1. « Rate limit dès le 1er appel »

### 1.1 `--provider-url` est parsé mais jamais utilisé — **bloquant**

`agent_mbpp.py:19` et `agent_swebench.py:30` déclarent l'argument `--provider-url`
(`required=True`), puis plus personne ne le lit. `src/llm.py:39` hardcode l'endpoint :

```python
api_url = "https://generativelanguage.googleapis.com/v1beta/openai"
```

À la correction, la moulinette passe l'URL du provider correspondant aux clés du `.env`.
Les clés partiront quand même chez Google → 401 / 403 / 429 immédiat →
`response.raise_for_status()` (`src/llm.py:76`) → `LLM API Error` à l'itération 1, et un
`solution.json` avec 0 step.

Le champ `api_url` de `StepMetrics` (obligatoire, §5.1) contient donc une valeur fausse
si un autre provider est utilisé.

### 1.2 Préfixe de clés incohérent avec l'endpoint — **bloquant**

`src/llm.py:11` : `TokenManager(provider_prefix="OPENROUTER_API_KEY")`, alors que l'URL
est celle de Gemini. Si le `.env` d'examen fournit `GEMINI_API_KEY` / `GOOGLE_API_KEY` /
`GROQ_API_KEY`, aucune clé n'est trouvée → `ValueError`.

Pire, `agent_mbpp.py:36-38` attrape cette erreur et fait un `return` **sans écrire
`solution.json`** :

```python
except ValueError as e:
    print(f"Startup Error: {e}")
    return
```

La tâche est perdue sans aucune trace exploitable.

### 1.3 Gestion du 429 contre-productive

`src/llm.py:57-64` :

```python
if response.status_code in [429, 402]:
    sleep_time = 20
    token_manager.rotate_key()
    time.sleep(sleep_time)
```

Si l'on change de clé, la nouvelle a **son propre quota** : il faut réessayer
immédiatement. Dormir 20 s après chaque rotation n'a de sens qu'une fois **tout le pool
épuisé**. Avec `max_retries=5`, un seul épisode de rate-limit coûte jusqu'à 100 s — soit
la quasi-totalité du budget MBPP (120 s).

Le commentaire parle d'« exponential backoff » mais le délai est constant à 20 s.

### 1.4 Modèle par défaut inexistant

`src/llm.py:35` : `model: str = "gemini-3.5-flash"`. Ce modèle n'existe pas.

---

## 2. « Trop de tokens »

### 2.1 Aucun `max_tokens` ni `stop` dans la requête — **cause principale**

`src/llm.py:50-53` n'envoie que `model` + `messages`. Le sujet insiste explicitement
(§5.6, encadré) :

> Use a `stop_sequences` (or `stop`) API parameter (e.g. `<end_code>`, `</tool_call>`) to
> stop generation at the token that ends a code block, otherwise the model may hallucinate
> fictional tool output instead of waiting for the real one.

Conséquence mesurée dans `cache/swebench_solution.json` :

| step | output_tokens | sandbox_output |
|---|---|---|
| 14 | 918 | `Code executed successfully without any output.` |
| 15 | 1 184 | idem |
| 17 | 800 | idem |
| 19 | 2 028 | idem |

≈ 4 900 tokens de sortie pour zéro information exploitable. Le run est à
**6 499 / 10 000 output tokens à l'itération 21** : la limite explose mécaniquement avant
d'atteindre 30 itérations.

**Fix :** `"max_tokens": N` + `"stop": ["<end_code>", "```\n"]` dans le payload.

### 2.2 Réponses vides non détectées (tokens de reasoning)

Dans `cache/mbpp_solution.json`, steps 1 et 2 :

```
step 1  input=187  output=108  llm_output=""
step 2  input=231  output=105  llm_output=""
```

Le modèle a consommé ~105 tokens de sortie et renvoyé un `content` vide (reasoning
tokens). `src/llm.py:87` accepte ce vide sans rien signaler :

```python
raw_text = message.get("content") or ""
```

Résultat : deux itérations sur dix gaspillées, des tokens brûlés, **et un `llm_output`
vide dans le JSON final** — champ que les évaluateurs inspectent précisément pour vérifier
la provenance du raisonnement (§6.4.1).

**Fix :** détecter le contenu vide → traiter comme un retry API (pas comme une itération),
et désactiver le thinking (`reasoning_effort: "none"`, ou
`extra_body: {"google": {"thinking_config": {"thinking_budget": 0}}}` selon le provider).
Le sujet le recommande : *« If your chosen model's reasoning tokens make limits tight,
consider using a non-reasoning model. »*

### 2.3 Budget MBPP réglé 10× trop haut

`agent_mbpp.py:66` : `max_input_tokens=60000`, alors que la limite est **6 000**.
Le garde-fou interne ne se déclenchera jamais avant que la moulinette n'invalide.

### 2.4 Historique jamais tronqué — croissance quadratique

`src/agent.py:32-35` initialise `history` et ne fait plus qu'y ajouter. L'input par step
croît en O(n²). Mesuré sur SWE-bench : 1 004 → 12 466 tokens/step, total 129 755 pour
21 steps. Extrapolé à 30 steps, on frôle les 300 000.

**Fix :** garder les N dernières observations en entier, compresser/résumer les
précédentes (ou ne conserver que le code et un extrait de sortie).

### 2.5 Les vérifications de limites arrivent trop tard

`src/agent.py:123-131` teste `current_total_input > max_input_tokens` **après** l'appel
LLM et **après** avoir enregistré le step. Le dépassement est déjà écrit dans `steps` →
`validate_metrics` invalide la solution.

**Fix :** estimer le coût de la requête avant de l'envoyer, et s'arrêter (ou compresser
l'historique) avant de franchir le seuil.

---

## 3. « N'y arrive pas en 30 itérations » (SWE-bench)

### 3.1 Timeout sandbox à 1 seconde appliqué aux appels MCP — **cause principale**

`src/sandbox.py:32` :

```python
max_execution_time_seconds: int = 1     # le sujet spécifie 30
```

et `src/sandbox.py:172` :

```python
process.join(self.config.max_execution_time_seconds + 1)   # 2 s de mur
```

Les appels d'outils MCP passent par ce même process enfant. Tout `docker exec` dépassant
2 s de temps mur est tué. `run_tests()`, qui lance le script d'évaluation (plusieurs
minutes), est **systématiquement tué**.

Le sujet est explicite (§5.2, encadré) :

> the sandbox restricts what LLM-generated Python code can do (imports, paths, timeout,
> memory), while **MCP tool actions happen outside the sandbox and are not subject to the
> sandbox timeout** (e.g. a tool spawning an external process).

Trace : step 9 → `TimeoutException - Max execution time reached.`

L'agent ne peut donc **jamais** vérifier son correctif : il tourne en rond jusqu'à
épuisement du budget.

### 3.2 `run_tests()` non configuré — **bloquant**

`mcp_tools_swebench.py:32` lit `SWEBENCH_TASK_FILE` pour charger `eval_script`.
`agent_swebench.py:64-66` ne transmet que `SWE_CONTAINER_NAME` et `TESTBED_PATH` :

```python
server_env = os.environ.copy()
server_env["SWE_CONTAINER_NAME"] = container_name
server_env["TESTBED_PATH"] = "/testbed"
```

Trace, step 4 :

```
Error: no evaluation script configured (set SWEBENCH_TASK_FILE).
Use run_command to run tests manually.
```

L'agent perd ensuite ~8 itérations (steps 5 à 11) à réinventer l'invocation de pytest à la
main : `pytest: not found`, mauvais interpréteur, mauvais environnement conda…

**Fix :** `server_env["SWEBENCH_TASK_FILE"] = str(Path(args.task_file).resolve())`.

### 3.3 Bug d'`exec` : les fonctions ne se voient pas entre elles — **bloquant MBPP**

`src/sandbox.py:143` :

```python
exec(code_string, safe_globals, {})
```

Avec un dict `locals` distinct du `globals`, toute fonction définie au niveau supérieur
atterrit dans `locals` et reste invisible depuis le corps des autres fonctions (qui
résolvent dans `globals`). Reproduit :

```python
def helper(x): return x * 2
def solve(n): return helper(n) + 1
solve(3)          # NameError: name 'helper' is not defined

def fact(n): return 1 if n <= 1 else n * fact(n - 1)
fact(5)           # NameError: name 'fact' is not defined
```

**Toute récursion et toute solution multi-fonctions échoue.** C'est fatal sur MBPP.

**Fix :** `exec(code_string, safe_globals)` (un seul dict, qui sert de globals et locals).

### 3.4 `max_iterations=130` alors que la limite est 30

`agent_swebench.py:140`. Même une réussite à l'itération 40 serait invalidée par
`validate_metrics` (`iterations 130 exceeds limit 30`). Le sujet demande en outre que
`max_iterations` soit un paramètre configurable de la boucle (§5.3.4).

### 3.5 `max_time_seconds=900` = exactement le SIGTERM

`agent_swebench.py:143`. La moulinette tue le process à 900 s ; l'agent n'a donc aucune
marge pour écrire `solution.json` ni nettoyer le conteneur. Viser ~840 s.
Idem MBPP : `max_time_seconds=120` (`agent_mbpp.py:68`) → viser ~100 s.

### 3.6 Aucun handler SIGTERM → fuite de conteneur Docker

`agent_swebench.py:153-158` nettoie dans un `finally`, ce qui ne s'exécute pas sur
SIGTERM/SIGKILL. Le sujet l'exige explicitement (§5.4 et §6.1) et le teste à l'examen
(`exam_swebench.sh` → *container cleanup*).

### 3.7 `sleep(5)` entre chaque itération

`src/agent.py:135-136`. 150 s brûlées sur 900 en SWE-bench, 45 s sur 120 en MBPP —
uniquement pour compenser l'absence de vraie stratégie de rate-limit (voir 1.3).

### 3.8 En cas d'échec, `solution` reste vide

`src/agent.py:39` initialise `final_solution = ""` et rien ne le remplit si
`final_answer()` n'est jamais appelé. Un patch partiel a une chance de passer ; une chaîne
vide, aucune.

**Fix :** au dernier tour (ou sur timeout imminent), forcer un `get_patch()` et le
soumettre.

### 3.9 Sur macOS, le sandbox SWE-bench ne démarre pas du tout

`multiprocessing` utilise `spawn` par défaut sur macOS. Les closures MCP construites en
`agent_swebench.py:79-90` ne sont pas picklables :

```
_pickle.PicklingError: Can't pickle <function ...>
  when serializing dict item 'read_file'
  when serializing dict item 'mcp_tools'
  when serializing src.sandbox.Sandbox state
```

Cela ne « fonctionne » sous Linux que par accident (`fork`). Le design actuel — passer des
callables MCP à travers une frontière de process — est fragile : il faut router les appels
d'outils vers le process parent (pipe/queue) plutôt que de forker le client MCP.

---

## 4. Ordre de correction recommandé

1. **`exec(code, safe_globals)`** + `max_execution_time_seconds=30` + sortir les appels MCP
   du timeout sandbox. → débloque MBPP *et* SWE-bench.
2. **`api_url = args.provider_url`** partout, `TokenManager` à préfixe configurable
   (multi-provider), retry immédiat après rotation de clé.
3. **`max_tokens` + `stop`** dans le payload, thinking désactivé, rejet des réponses vides.
   → règle l'explosion des output tokens.
4. **Passer `SWEBENCH_TASK_FILE`** au serveur MCP → `run_tests()` fonctionnel.
5. **Aligner les limites** (MBPP 10 / 6 000 / 1 500 / ~100 s ; SWE 30 / 300 k / 10 k /
   ~840 s), vérifier *avant* l'appel, tronquer l'historique.
6. **Handler SIGTERM** pour le cleanup Docker + `final_answer(get_patch())` forcé au
   dernier tour.

---

## 5. Manques structurels vis-à-vis du sujet (indépendants des bugs)

Ces points ne causent pas les symptômes rapportés mais font échouer l'évaluation.

- **CLI `uv run sandbox` absente** (§5.2.1). Le sujet exige un REPL interactif, plus les
  variantes `uv run sandbox sandbox_template.json`, `--mcp-stdio "..."`, `--mcp-server
  <URL>`. Rien dans `pyproject.toml` (`[project.scripts]`) ni dans `src/`.
- **`run_tests(code, test_list)` manquant côté MBPP** (§5.3.2). `mcp_tools_mbpp.py`
  n'expose que `check_syntax`. Outil obligatoire.
- **Aucun outil MCP branché sur l'agent MBPP** : `agent_mbpp.py:28` passe
  `mcp_tools = {}`, et le manuel du sandbox est une chaîne écrite en dur
  (`agent_mbpp.py:29`) au lieu d'être généré depuis les schémas MCP (§5.2.5).
- **Prompt système SWE-bench en dur** (`agent_swebench.py:102-111`) au lieu d'utiliser
  `mcp_client.get_sandbox_manual()`. Le sujet exige que le manuel reflète automatiquement
  le serveur MCP connecté — *« The system will be tested with an unknown MCP server »*.
- **Transport HTTP streamable non supporté** : `src/mcp_client.py` implémente stdio et SSE.
  Le sujet demande stdio **et streamable HTTP** (§5.2.4).
- **`README.md` vide.** Le chapitre VII impose : première ligne en italique
  (`This project has been created as part of the 42 curriculum by <login>`), sections
  Description / Instructions / Resources, plus architecture système, explication de la
  boucle d'agent, design du sandbox, détails d'implémentation des outils, résultats de
  benchmark.
- **`BENCHMARK_REPORT.md` absent** (§5.7) : ≥ 5 modèles × ≥ 2 providers × ≥ 3 tâches
  SWE-bench, table de résultats, fiabilité des providers, ≥ 2 métriques intermédiaires,
  ≥ 1 ablation, conclusions — avec les `solution.json` correspondants versionnés.
- **Pas de fichier de configuration sandbox** (`sandbox_template.json`) alors que le sujet
  demande une config pilotée par JSON + Pydantic.

### Points de sécurité à revoir (testés par `exam_sandbox.sh`)

- `src/sandbox.py:45-50` copie **tous** les builtins et n'en retire que 6. `getattr`,
  `type` et `__build_class__` restent disponibles → évasion classique par
  `().__class__.__base__.__subclasses__()` pour atteindre `subprocess.Popen`.
- `_disable_network` (`src/sandbox.py:104-112`) ne remplace que `socket.socket`.
  À compléter (au minimum `socket.create_connection`, `socket.socketpair`,
  `socket.getaddrinfo`).
- `except BaseException` (`src/sandbox.py:155`) attrape `KeyboardInterrupt`, ce que le
  sujet interdit explicitement : *« KeyboardInterrupt and SystemExit must not be silently
  caught — they need to reach the agent loop for proper shutdown. »*
