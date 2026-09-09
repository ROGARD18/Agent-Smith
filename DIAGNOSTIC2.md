# Agent Smith — Diagnostic #2

Ré-analyse après les commits `05f0a36` (« modif errors finds ») et `4454687` (« fix: tool
calling »), qui répondent au premier diagnostic (`DIAGNOSTIC.md`).

**Verdict court :** le sandbox a été correctement réparé — c'était le vrai point bloquant
et c'est réglé. Mais trois régressions ont été introduites, dont **deux qui rendent MBPP
et toute définition de classe totalement inopérants**, et les causes du symptôme
« rate limit dès le 1er appel » n'ont pas été traitées.

Chaque point ci-dessous est vérifié par exécution réelle du code, pas par lecture.

---

## 2. Régressions introduites — bloquantes

### 2.1 `agent_mbpp.py` crashe au démarrage à chaque exécution

`agent_mbpp.py:57` :

```python
token_manager = TokenManager(api_url=args.provider_url)
```

`TokenManager.__init__` (`src/llm.py:11`) a pour seule signature :

```python
def __init__(self, provider_prefix: str = "OPENROUTER_API_KEY"):
```

Il n'existe **aucun paramètre `api_url`**. Vérifié :

```
TypeError: TokenManager.__init__() got an unexpected keyword argument 'api_url'
caught by 'except ValueError'?  False
```

Le `try/except ValueError` de `agent_mbpp.py:58` n'attrape pas un `TypeError` : l'exception
remonte, le process meurt, **aucun `solution.json` n'est écrit**. MBPP est à 0/5,
systématiquement, avant même le premier appel LLM.

Accessoirement `agent_swebench.py:85` appelle `TokenManager()` sans argument : les deux
points d'entrée ne sont même pas d'accord entre eux.

### 2.2 Toute définition de classe est cassée

`src/sandbox.py:50` retire `__build_class__`, `type` et `getattr` des builtins :

```python
dangerous_builtins = {'eval','exec','compile','globals','locals','vars','input',
                      'getattr','setattr','delattr','type','__build_class__'}
```

`__build_class__` est le builtin qu'émet le compilateur pour **toute** instruction `class`.
Vérifié dans le sandbox réel :

```
definition de classe   -> error | 'NameError: __build_class__ not found'
type()                 -> error | "NameError: name 'type' is not defined"
getattr()              -> error | "NameError: name 'getattr' is not defined"
```

Conséquences : impossible de définir une classe (fréquent en MBPP, systématique dans les
scripts de reproduction SWE-bench), `type(x) is int` et `getattr(obj, 'attr')` cassent du
code parfaitement légitime. L'agent va tourner en boucle sur des `NameError`
incompréhensibles pour lui.

**Et l'évasion visée n'est pas bloquée.** Vérifié dans le même sandbox :

```
evasion __subclasses__ -> observation | '484\n'
```

`().__class__.__base__.__subclasses__()` passe toujours : la syntaxe d'accès aux attributs
ne transite pas par le builtin `getattr`. Le durcissement casse le code honnête sans
fermer le trou.

**Correction :** remettre `getattr`, `type` et `__build_class__`, et bloquer réellement
l'évasion — soit par un `__builtins__` reconstruit en allowlist (on ne garde que ce dont on
a besoin) plutôt qu'en denylist, soit par un audit hook (`sys.addaudithook`) sur les
attributs dunder, soit en interdisant les identifiants `__class__`, `__subclasses__`,
`__globals__`, `__mro__` au niveau de l'AST avant exécution.

### 2.3 Backoff : jusqu'à 12 jours de sommeil

`src/agent.py:74` passe `max_retries=20`, alors que `src/llm.py:68` et `:104` gardent
`sleep_time = 2 ** attempt` :

```
cumul sleep 5xx/network: 1048575 s = 12,1 jours
```

Avant, `max_retries=5` plafonnait à 16 s. Maintenant, une série d'erreurs 5xx ou réseau
endort l'agent bien au-delà de tout timeout : la moulinette le SIGKILL, aucun
`solution.json` n'est produit. Il faut plafonner (`min(2 ** attempt, 8)`) et compter le
budget temps global.

---

## 3. Ce qui n'a pas été corrigé

### 3.1 `--provider-url` toujours ignoré

`src/llm.py:39` a simplement changé de valeur en dur :

```python
api_url = "https://openrouter.ai/api/v1"   # avant : generativelanguage.googleapis.com
```

L'argument `--provider-url`, `required=True` dans les deux agents, n'est toujours lu nulle
part. Le sujet impose de pouvoir changer de provider sans refactor (§5.6) et le champ
`api_url` de `StepMetrics` doit refléter l'endpoint réellement utilisé.

Corollaire : `src/llm.py:35` garde `model = "gemini-3.5-flash"` par défaut — un identifiant
qui n'existe ni chez Google ni chez OpenRouter (dont les ids sont de la forme
`google/gemini-2.0-flash-exp:free`).

### 3.2 Toujours aucun `max_tokens` ni `stop`

`src/llm.py:50-53` envoie toujours uniquement `model` + `messages`. C'était la cause
principale de l'explosion des output tokens (steps à 918, 1 184, 2 028 tokens pour zéro
information dans l'ancienne trace), et le sujet le demande explicitement (§5.6, encadré
sur `stop_sequences`). Non traité.

### 3.3 La dernière trace montre un échec au premier appel

`cache/swebench_solution.json` (run du 2026-09-09) :

```
iterations: 0   total_requests: 0   total_input_tokens: 0   total_time_seconds: 42.7
error: "LLM API Error: Max retries exceeded across all available API keys..."
```

42,7 s ≈ 20 tentatives × 2 s : les 20 retries se sont enchaînés sur des 429/402 avant
d'abandonner. Deux choses à regarder :

- **`src/llm.py:57` traite le 402 comme un 429.** OpenRouter renvoie **402** quand le
  compte n'a pas de crédits, et **429** quand le quota gratuit journalier est épuisé. Le
  402 ne se résoudra jamais par un retry : le réessayer 20 fois ne fait que perdre 40 s et
  masquer le vrai message d'erreur. Il faut les séparer, et logger `response.text`.
- Le modèle passé en `--model-name` doit être un id OpenRouter valide **avec le suffixe
  `:free`**, sinon le compte est facturé (interdit par le sujet) ou refusé.

### 3.4 Manques structurels (inchangés)

- **CLI `uv run sandbox` absente** (§5.2.1) : REPL interactif, `sandbox_template.json`,
  `--mcp-stdio`, `--mcp-server <URL>`. Rien dans `pyproject.toml` (`[project.scripts]`).
- **`sandbox_template.json` absent** alors que le sujet demande une config JSON + Pydantic.
- **Transport streamable HTTP absent** : `src/mcp_client.py:41` n'implémente que SSE.
  Le sujet exige stdio **et** streamable HTTP (§5.2.4).
- **`BENCHMARK_REPORT.md` absent** (§5.7) : ≥ 5 modèles × ≥ 2 providers × ≥ 3 tâches, table
  de résultats, fiabilité providers, ≥ 2 métriques intermédiaires, ≥ 1 ablation,
  conclusions, avec les `solution.json` correspondants versionnés.
- **`README.md` contient le mot `pro`.** Le chapitre VII impose : première ligne en
  italique (`This project has been created as part of the 42 curriculum by <login>`),
  sections Description / Instructions / Resources, plus architecture système, explication
  de la boucle d'agent, design du sandbox, détails d'implémentation des outils, résultats
  de benchmark. En anglais.

---

## 4. Nouveaux problèmes de fiabilité des métriques

Le sujet (§6.4.1) précise que `steps`, `llm_output`, `total_requests` servent à vérifier
que les chiffres viennent d'une exécution réelle. Trois incohérences ont été introduites.

### 4.1 Les réponses vides consomment des tokens qui ne sont jamais comptés

`src/agent.py:84-87` :

```python
if not raw_text.strip():
    observation = "Error: LLM returned an empty response. ..."
    history.append({"role": "user", "content": f"Observation:\n{observation}"})
    continue
```

Le `continue` saute la construction du `StepMetrics`. Les `input_tokens` /
`output_tokens` de cet appel — 105 à 108 tokens par réponse vide dans l'ancienne trace MBPP
— **ne sont comptés nulle part**. L'agent sous-déclare sa consommation réelle, et son
propre garde-fou de budget (`src/agent.py:52-53`, qui somme `steps`) est aveugle à ces
appels. Sur 6 000 tokens de budget MBPP, quelques réponses vides suffisent à fausser le
compte.

Il faut enregistrer un `StepMetrics` (avec `llm_output=""`, `sandbox_input=""`,
`sandbox_output="<empty response>"`) ou, mieux, traiter le vide comme un **retry API** à
l'intérieur de `generate_chat_response` — il ne consomme alors pas d'itération et ses
tokens sont agrégés dans le step suivant.

Effet de bord : ce `continue` ajoute un message `user` sans message `assistant` avant lui,
donc deux `user` consécutifs dans `history`. Certains providers refusent ou fusionnent ces
tours.

### 4.2 `total_requests` reste à 0 quand tout échoue

`src/agent.py:76` n'incrémente `total_requests` qu'après un retour réussi. Dans la dernière
trace, 20 requêtes HTTP ont réellement été émises et le JSON annonce `total_requests: 0`.
Le sujet définit ce champ comme « Total number of LLM API requests made (**including
retries**) ».

### 4.3 Le patch de secours est déclaré comme un succès

`src/agent.py:155-165`, en fin de boucle, récupère `get_patch()` et fait :

```python
success = True   # Optionnel: on le passe à True si on considère qu'avoir un patch est un succès
```

Le `solution.json` sort alors avec `success: true` **et** `error: "Failed: Max iterations
reached (30)"` — deux affirmations contradictoires — pour un patch que l'agent n'a jamais
vérifié. `success` est défini par le sujet comme « whether the agent **believes** it solved
the task ». Garder le patch dans `solution` est la bonne idée ; le déclarer réussi ne l'est
pas. Mettre `success = False`.

Il y a par ailleurs **deux mécanismes de secours concurrents** : celui de `src/agent.py:155`
et celui de `agent_swebench.py:132`. Le second ne s'exécute jamais, puisque le premier a
déjà mis `success=True`. Et il ne filtre pas les messages d'erreur : c'est ainsi que
`cache/swebench_solution.json` contient

```json
"solution": "No changes made yet (empty diff)."
```

c'est-à-dire une phrase en anglais là où la moulinette attend un diff git.

---

## 5. Points secondaires

- **`RLIMIT_AS` échoue sur macOS.** `src/sandbox.py:111` lève
  `ValueError: current limit exceeds maximum limit` sur Darwin (RLIMIT_AS n'y est pas
  applicable). L'exception est attrapée par le `except Exception` de `:162` et remontée
  comme une erreur d'exécution du code de l'agent. Résultat : **sur macOS, 100 % des
  exécutions sandbox échouent** avec ce message, quel que soit le code. Sur Linux (machine
  de correction) ça fonctionne — mais tant que ce n'est pas géré, vous ne pouvez pas tester
  votre agent en local. Envelopper le `setrlimit` dans un `try/except` **et signaler
  explicitement** quand une limite de sécurité n'a pas pu être posée.
- **Pas de contrôle `process.is_alive()` dans la boucle du parent** (`src/sandbox.py:182`).
  Si l'enfant meurt sans écrire dans la queue (SIGXCPU sur RLIMIT_CPU, crash au démarrage,
  OOM killer), le parent attend les 30 s complètes et rapporte un
  `TimeoutException` trompeur. Observé pendant les tests : un enfant qui n'a jamais démarré
  a coûté 30,0 s et produit « Max execution time reached ». Sur MBPP (100 s de budget),
  un seul incident consomme 30 % du temps.
- **`except SystemExit: pass`** (`src/sandbox.py:160-161`) : le sujet demande que
  `SystemExit` et `KeyboardInterrupt` remontent à la boucle d'agent. Ici un `sys.exit()`
  du code utilisateur est avalé sans message dans la queue → le parent attend 30 s puis
  déclare un timeout.
- **Troncature d'historique très agressive.** `src/agent.py:66-67` :
  `history = history[:2] + history[-6:]` ne conserve que **les 3 derniers échanges**. Ça
  règle la croissance quadratique, mais sur 30 itérations SWE-bench l'agent oublie ce qu'il
  a déjà exploré : il relit les mêmes fichiers, refait les mêmes recherches, perd la trace
  de son propre patch. Préférer une compression : garder tous les `sandbox_input`, ne
  tronquer que les `sandbox_output` anciens à quelques centaines de caractères.
- **`safe_open` compare par préfixe** (`src/sandbox.py:78`) : `startswith('/testbed')`
  autorise aussi `/testbedXXX`. Utiliser `os.path.commonpath` ou imposer le séparateur.
- **Code mort** : `src/agent.py:141-143` et `:149-152` font le même travail deux fois,
  `total_time` est calculé en `:145` puis écrasé en `:167`.
- **Budget qui peut passer négatif** : `src/sandbox.py:185`,
  `request_queue.get(timeout=max(0.1, time_budget))` — après beaucoup d'appels d'outils,
  `time_budget` peut tomber sous zéro et le sandbox n'a plus que 0,1 s pour son code Python.

---

## 6. Ordre de correction

| # | Action | Fichier | Effet |
|---|---|---|---|
| 1 | Retirer `api_url=` ou ajouter le paramètre à `TokenManager` | `agent_mbpp.py:57` / `src/llm.py:11` | débloque MBPP (actuellement 0/5) |
| 2 | Remettre `getattr`, `type`, `__build_class__` ; bloquer l'évasion par allowlist ou AST | `src/sandbox.py:50` | débloque les classes et le code normal |
| 3 | Plafonner le backoff (`min(2**attempt, 8)`), séparer 402 et 429 | `src/llm.py:68,104,57` | évite le SIGKILL et les 40 s perdues |
| 4 | Lire `--provider-url` ; corriger le modèle par défaut | `src/llm.py:39`, les 2 agents | conformité §5.6 + provenance `api_url` |
| 5 | Ajouter `max_tokens` et `stop` au payload | `src/llm.py:50` | contient les output tokens |
| 6 | Compter les réponses vides et `total_requests` réels ; `success=False` sur patch de secours ; supprimer le double salvage | `src/agent.py`, `agent_swebench.py` | métriques honnêtes et cohérentes |
| 7 | `try/except` sur `setrlimit` + `process.is_alive()` dans la boucle | `src/sandbox.py:111,182` | testable en local, erreurs justes |
| 8 | Compression d'historique au lieu de la troncature à 3 échanges | `src/agent.py:66` | l'agent garde sa mémoire d'exploration |
| 9 | CLI `uv run sandbox`, `sandbox_template.json`, transport HTTP, README, BENCHMARK_REPORT | — | conformité au sujet |

Les points 1 et 2 sont à faire en premier : tant qu'ils tiennent, aucune mesure sur MBPP
n'est exploitable.
