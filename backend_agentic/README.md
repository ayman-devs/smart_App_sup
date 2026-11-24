# Backend RAG agentique multimodal (local)

Ce backend répond à des questions sur `data/Brochure-EMSI.pdf`, à partir du texte et des images du PDF.
Il tourne entièrement sur ton PC, sans clé d'API payante. L'app Flutter l'appelle en HTTP.

## Prérequis (à installer une seule fois)

| Outil | Rôle | Vérifier |
|---|---|---|
| Python 3.11 | exécute le backend | `python --version` |
| Ollama | fait tourner les modèles d'IA en local | `ollama --version` |
| Docker Desktop | fait tourner Qdrant, la base de vecteurs | `docker --version` |

Modèles Ollama à télécharger (environ 4,5 Go au total) :
```bash
ollama pull bge-m3                 # embeddings de texte multilingues
ollama pull qwen3-vl:4b-instruct   # modèle vision-langage : lit, voit des images, appelle des outils
```
Au premier lancement, les modèles CLIP et le reranker (environ 1 Go) sont téléchargés automatiquement depuis Hugging Face.

## Installation

```bash
cd backend_agentic
python -m venv .venv
.venv\Scripts\activate            # Windows (macOS/Linux : source .venv/bin/activate)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

## Lancer

```bash
docker compose up -d               # 1. Qdrant sur http://localhost:6333
python -m app.ingest               # 2. indexer data/ (à refaire si tu changes de PDF)
uvicorn app.main:app --port 8000   # 3. l'API (et le serveur MCP sur /mcp/)
```

- Documentation interactive : http://localhost:8000/docs
- État du système : http://localhost:8000/health (`"status": "ok"` quand tout est prêt)

Exemple de requête :
```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
     -d "{\"question\": \"Quelles filières propose l'EMSI ?\", \"mode\": \"classic\"}"
```

## Tester

```bash
pytest
```
Les tests n'ont besoin ni d'Ollama ni de Docker : les modèles sont remplacés par de faux modèles et Qdrant tourne en mémoire.

Pour comparer les 3 modes sur `eval/questions.txt`, avec l'API lancée (les lignes qui commencent par `!` sont des questions pièges, auxquelles la brochure ne répond pas) :
```bash
python -m eval.compare
```

## Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/health` | état de Qdrant, des documents indexés et des modèles |
| POST | `/chat` | `{question, image_base64?, mode: "classic"\|"agent"\|"multi", conversation_id?, history?}` |
| POST | `/agent/chat` | mode agent avec mémoire (`conversation_id`) |
| POST | `/agent/reset` | `{conversation_id}` : efface la mémoire de cette conversation (modes agent et multi) |
| POST | `/compare` | même question aux 3 modes |
| GET | `/images/<fichier>` | images extraites du PDF |
| — | `/mcp/` | serveur MCP (outils de l'agent) |

Format de la réponse de `/chat` :
```json
{
  "mode": "classic",
  "answer": "…",
  "sources": [{"source": "Brochure-EMSI.pdf", "page": 4}],
  "images": [{"image_id": "…", "url": "/images/….png", "page": 4, "similarity": 0.31}],
  "steps": [{"tool": "search_document", "args": {"query": "…"}}],
  "seconds": 3.2
}
```

## Les 3 modes

| Mode | Fonctionnement | Durée typique |
|---|---|---|
| `classic` | 1 recherche, puis 1 appel au modèle | 8–20 s |
| `agent` | 1 agent ReAct avec les 5 outils et une mémoire | 5–25 s |
| `multi` | 4 agents coopèrent (voir ci-dessous) | 10–40 s |

### Mode multi-agents (`app/multi_agent.py`)

```
question ─▶ 1. SUPERVISEUR ──délègue──▶ 2. DOCUMENT  (search_document, get_page)
                 │          └──────────▶ 3. VISION    (identify_image, look_at_image, search_images)
                 ▼ brouillon
            4. VÉRIFICATEUR (get_page) ─▶ réponse finale
```

- **Superviseur** : comprend la question et délègue. Ses « outils » sont les deux agents spécialistes (principe *agents-as-tools*). Si la question a plusieurs parties, il délègue une tâche par partie.
- **Document** : cherche dans le texte de la brochure. Pour une liste (filières, campus…), il fait plusieurs recherches.
- **Vision** : voit la photo envoyée, la retrouve dans la brochure et rapporte ce que la brochure en dit. Il est toujours appelé quand une photo est envoyée.
- **Vérificateur** : compare le brouillon avec les extraits trouvés par les agents, et avec les réponses déjà vérifiées de la conversation. Il retire tout ce qui n'est pas dans la brochure.

**Vérification automatique** (`app/fact_check.py`, du code et non de l'IA). Un petit modèle qui vérifie un autre modèle a tendance à tout valider : en test, des villes inventées (Agadir, Meknès, Oujda) sont passées. Après le vérificateur, le code fait donc deux contrôles :
1. **Noms propres et nombres** : chaque nom propre et chaque nombre de la réponse doit apparaître dans les extraits trouvés. Si ce n'est pas le cas, le vérificateur refait sa réponse en recevant la liste des mots inventés. S'il en reste, le code les retire.
2. **Photo** : si une photo a été envoyée, le code sait avec certitude si elle est dans la brochure (similarité CLIP ≥ 0,9). Une phrase qui affirme le contraire est corrigée.

Les 4 agents utilisent le même modèle local, car le GPU ne peut en charger qu'un. Ce qui les distingue, ce sont leurs instructions et les outils auxquels ils ont accès. L'ensemble forme un graphe LangGraph `START → supervisor → verifier → END`. Sa mémoire ne garde que les questions et les réponses vérifiées.

La réponse de `/chat` en mode `multi` contient en plus :
- `agents` : par exemple `["Superviseur", "Document", "Vérificateur"]` ;
- `draft` : le brouillon avant vérification, utile pour le rapport ;
- dans chaque élément de `steps`, un champ `agent` qui indique quel agent a appelé quel outil.

### Résultats mesurés (`python -m eval.compare`, GPU RTX A2000 8 Go)

| Question | Classique | Agent | Multi-agents |
|---|---|---|---|
| Filières de l'EMSI | 13 s | 16 s | 19 s |
| Villes des campus | 11 s | 9 s | 12 s (p. 2) |
| Directeur général | 11 s | 8 s | 10 s (p. 5) |
| Partenariats et diplômes (2 parties) | 9 s | 16 s | 38 s (4 délégations) |
| Questions pièges refusées | 3/3 | 3/3 | 3/3 |

Le mode multi-agents est le plus lent, mais c'est le seul qui vérifie sa réponse. Par exemple, il a retiré des villes inventées qui figuraient dans le brouillon.

## Améliorations de la recherche

- **Résumés de pages** (*contextual retrieval*, dans `ingest.py`) : à l'indexation, le modèle écrit une phrase de résumé par page, indexée comme un passage de plus. La page 2, « TANGER CASABLANCA RABAT… 19 CAMPUS », ne contient pas le mot « villes ». Son résumé, lui, permet de la trouver. Chaque résumé passe par la vérification automatique et ne peut donc rien inventer. Pour désactiver cette option : `PAGE_SUMMARIES=false`.
- **Majuscules normalisées** (« TANGER » → « Tanger ») pour le calcul des vecteurs et pour le reranker.
- **Reranker combiné** (par RRF) avec la recherche hybride, au lieu de la remplacer. Seul, il faisait passer un passage exact du 1ᵉʳ au 9ᵉ rang.
- **Recherche d'images vérifiée** (`search_images` dans `tools.py`) : les scores CLIP texte → image sont presque égaux (0,23 à 0,26). Pour « étudiants en blouse blanche », la bonne photo n'arrivait qu'au 6ᵉ rang. CLIP propose donc 8 candidates, et le modèle vision les examine une par une (« montre-t-elle TOUS les détails ? oui/non »). L'outil renvoie la première qui correspond, ou aucune.
- **Nouvelle photo = nouvelle question** : en mode multi-agents, quand une photo est envoyée, les agents ne relisent pas les réponses précédentes. Sinon, ils décrivaient la photo de la question d'avant.
- **Longueur de réponse limitée** à 1 024 tokens, pour couper un modèle qui tourne en boucle.

## Historique des conversations (Supabase)

Tous les chats sont enregistrés dans Supabase, par l'app Flutter (`lib/services/chat_history.dart`) :
- la table `conversations` (titre = 1ʳᵉ question, date) ;
- la table `chat_messages` (texte, et pour les réponses : pages, image, agents et outils dans `details`) ;
- le bucket privé `chat-photos` pour les photos envoyées, dans un dossier par utilisateur.

La sécurité est assurée par la base (Row Level Security) : chaque utilisateur ne voit et n'écrit que ses propres chats. Le SQL se trouve dans `supabase/migrations/20261124_chat_history.sql`.

**Reprendre un ancien chat.** La mémoire des agents est en RAM, donc elle est perdue quand le backend redémarre. C'est pourquoi l'app envoie avec chaque question le champ `history` : les messages précédents, `[{role, content}]`. Si le backend n'a plus cette conversation en mémoire, il la reconstruit à partir de `history` (`restore_memory` dans `agent.py`). Sinon, il ignore ce champ. L'identifiant de la conversation dans Supabase sert aussi de `conversation_id`.

## Côté Flutter

- L'adresse du backend est définie à un seul endroit : `lib/backend_config.dart`.
  - Web, Windows et simulateur iOS : `http://localhost:8000`.
  - Émulateur Android : `http://10.0.2.2:8000`.
  - Vrai téléphone : `flutter run --dart-define=BACKEND_URL=http://<IP-du-PC>:8000`.
    Dans ce cas, lance l'API avec `--host 0.0.0.0`.
- Le code de la page de chat est dans `lib/screens/chat_page.dart`, et celui des appels HTTP dans `lib/services/chat_api.dart`.

## Points à connaître

- **Premier lancement lent** : l'API télécharge les modèles CLIP et le reranker depuis Hugging Face. Avec une connexion lente, cela peut prendre plusieurs minutes. Ensuite, une question prend environ 10 à 25 secondes.
- **Taille du contexte** (`OLLAMA_NUM_CTX=16384`) : avec la valeur par défaut d'Ollama (environ 4 000 tokens), les réponses de l'agent étaient coupées au milieu.
- **Images sans photo** (mode classique) : seules les images des pages citées sont affichées. Les scores CLIP texte → image ne sont pas fiables seuls, car le logo EMSI ressort pour n'importe quelle question.
- **Photo envoyée** : elle est reconnue si la similarité est ≥ 0,9. On obtient environ 0,96 pour la même image, même recadrée et compressée, et environ 0,83 pour une photo seulement ressemblante.

## Réglages

Tous les réglages sont dans `app/config.py`. Tu peux les remplacer avec un fichier `.env`, par exemple :
```
OLLAMA_VLM_MODEL=qwen3-vl:8b-instruct
TOP_K_FINAL=6
```

L'audio (Whisper), présent dans le document d'architecture, n'est volontairement pas inclus.
