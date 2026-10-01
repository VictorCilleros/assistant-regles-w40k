# Assistant de règles — Spécification technique, version 1

## Objectif et périmètre

**Objectif :** un assistant qui répond à des questions de règles en s'appuyant uniquement sur le corpus, avec citations vérifiables, abstention quand le corpus ne permet pas de conclure, et une évaluation chiffrée de chaque brique.

**Corpus (v1) :** le livre de règles de base de Warhammer 40 000, 11e édition (PDF français, 100+ pages). Données traitées **en local**, non redistribuées.

**Principe directeur (inchangé) :** on mesure d'abord ; toute brique ajoutée doit améliorer un chiffre de l'évaluation. Ce principe a conduit à adopter l'agent de recherche et à écarter la recherche plein texte (voir section 9).

**Livré en v1 :** ingestion, indexation, recherche dense, agent de recherche, génération citée, interface Streamlit, commandes en ligne, évaluation et suite de tests.

---

## Récapitulatif des choix

| Composant | Choix v1 | Note |
|---|---|---|
| Parsing PDF | **Docling** (auto-hébergé, layout-aware) | Cache de la conversion ; texte normalisé (accents, guillemets, ligatures) |
| Découpage | Une sous-section codée par passage, préfixe de chemin | Cible 400 tokens, maximum 600, sans chevauchement |
| Embedding | **BGE-M3** dense via sentence-transformers, révision épinglée | 1024 dimensions, pooling CLS, fp16 sur GPU, renormalisé en float32 |
| Base vectorielle | **PostgreSQL 18 + pgvector 0.8.6** (Docker Compose) | Table unique `chunks_w40k`, index HNSW cosinus |
| Recherche | Dense, top-k cosinus, k = 5 | Garde-fou : même modèle en base et pour la question |
| Agent de recherche | **Claude Sonnet 5**, effort `high` | 3 outils ; sélection de 5 à 8 passages |
| Génération | **Claude Sonnet 5**, effort `medium` | Citations natives (`search_result`), par paragraphe, streaming |
| Interface | **Streamlit** + commandes en ligne | Mode agent activable, étapes de l'agent en direct |
| Évaluation | Métriques de retrieval maison + **RAGAS** (juge Claude Haiku 4.5) | Gold set de 100 questions, notebook 08 |
| Configuration | YAML validés par Pydantic (`extra="forbid"`) | Une clé inconnue fait échouer le chargement |
| Tests | pytest ; marqueurs `integration` (testcontainers), `modele`, `api` | Suite par défaut sans base, sans modèle, sans API |

---

## 1. Données et pré-processing

**Commande :** `uv run ingest-regles`. **Sortie :** `data/interim/chunks.jsonl` (environ 207 passages).

1. **Extraction (Docling).** Le PDF étant natif, aucun OCR n'est nécessaire. La conversion est mise en cache pour ne pas être refaite à chaque exécution.
2. **Structure.** Les chapitres, sections et pages sont décrits dans `config/ingest/structure_livre.yaml`, validé au chargement (sections ordonnées, chapitres sans chevauchement). Les codes de règle « XX.YY » sont extraits du texte.
3. **Nettoyage.** Sont exclus : pages liminaires, introduction, index, ouvertures de chapitre, numéros de page ou de section, coûts isolés, renvois courts, étiquettes de schémas, lore des stratagèmes, doublons. Tous les éléments, exclus compris, sont conservés dans `elements.parquet` avec leur motif d'exclusion, pour l'audit.
4. **Normalisation du texte.** Sans NFKC sur le corps du texte : caractères de contrôle supprimés, ligatures décomposées, apostrophes et guillemets anglais unifiés ; « ½ », pouces, « « » » et mots-clés en majuscules préservés.
5. **Découpage.**
   - unité de base : une sous-section codée (ou une introduction de section sans code) ;
   - 600 tokens BGE-M3 ou moins : un seul passage ;
   - au-delà : regroupement de sous-parties contiguës jusqu'à environ 400 tokens, **sans chevauchement**, le contexte étant porté par le préfixe ;
   - filet de sécurité : découpage par paragraphes avec chevauchement d'un élément, si une sous-partie seule dépasse 600 tokens (jamais le cas sur ce livre) ;
   - un tableau n'est jamais coupé ; les titres de sous-parties sont réinsérés dans le texte.
6. **Préfixe de contexte.** Chaque passage commence par son chemin : « CHAPITRE > NN Section > NN.NN Titre ».
7. **Métadonnées par passage :** `id` (uuid5 déterministe sur la source et les éléments Docling), `texte`, `chapitre`, `section_num`, `section_titre`, `code`, `sous_section`, `sous_parties`, `page_debut`, `page_fin`, `type_contenu` (`regle` ou `table`), `edition`, `source`, `codes_cites` (renvois vers d'autres règles), `partie`, `nb_parties`, `nb_tokens`, `hors_budget`, `ordres`.

**Statistiques :** médiane d'environ 150 tokens par passage, maximum 568.

**Limites connues :** les encadrés latéraux peuvent être rattachés à la sous-section voisine ; le code 15.11 existe deux fois (démonstration p. 55, règle p. 57) ; le coût en points de commandement des stratagèmes n'est pas encore une métadonnée.

---

## 2. Modèle d'embedding

- **BGE-M3, sortie dense**, via sentence-transformers, à la **révision épinglée** `5617a9f6…` (commit Hugging Face).
- **Dimension 1024**, vérifiée au chargement contre la configuration et contre la colonne de la base.
- **Pooling CLS**, vérifié en le recalculant à la main (cosinus de 1,0000 avec la sortie de sentence-transformers, contre 0,77 pour un mean pooling).
- **fp16 sur GPU** : cosinus minimal de 0,9997 entre fp16 et fp32 sur tout le corpus. Les vecteurs sont **renormalisés en float32** après l'encodage, le fp16 laissant des normes entre 0,9997 et 1,0005.
- **Longueur :** maximum de 8192 tokens, très au-delà des passages (568 tokens au plus). Un contrôle explicite refuse tout texte qui serait tronqué en silence.
- **Pas de préfixe d'instruction** pour les questions : BGE-M3 encode questions et passages de la même façon.
- Le mode hors ligne de Hugging Face (`HF_HUB_OFFLINE=1`) évite tout appel réseau une fois le modèle en cache.

---

## 3. Base vectorielle

- **PostgreSQL 18 + pgvector 0.8.6** dans un conteneur (`compose.yaml`), port exposé sur `127.0.0.1` uniquement.
- **Table unique `chunks_w40k`** pour toutes les sources : les colonnes `source` et `edition` délimitent les périmètres (règles de base aujourd'hui, règles d'armée demain).
- Colonnes : toutes les métadonnées des passages (listes en `text[]` et `integer[]`), plus `embedding vector(1024)`, `modele_embedding` (nom et révision), `empreinte_texte` (sha256) et `indexe_le`.
- **Index HNSW** cosinus (`m = 16`, `ef_construction = 64`). À cette échelle, le planificateur préfère un scan séquentiel, qui est exact ; l'index est utilisé dès qu'on désactive les scans séquentiels, et prendra le relais avec un corpus plus grand.
- **Schéma idempotent** (`sql/schema.sql`), appliqué par le code au lancement de l'indexation et par les tests sur une base vierge.

### Indexation

**Commande :** `uv run indexer-regles`.

1. application du schéma et vérification de la dimension, **avant** l'encodage ;
2. encodage des passages ;
3. **synchronisation dans une seule transaction** : upsert sur l'`id` (`INSERT … ON CONFLICT DO UPDATE`), puis suppression des **orphelins** du périmètre (même source et même édition, `id` absent du lot). Une liste d'identifiants vide est refusée, sans quoi la suppression viderait tout le périmètre ;
4. **contrôles** : nombre de lignes, dimensions, normes, et auto-récupération (chaque passage est son propre plus proche voisin).

---

## 4. Recherche

- La question est encodée par le même encodeur, puis comparée aux passages par distance cosinus (`<=>`). Le score affiché est `1 - distance`, soit la similarité cosinus.
- **k = 5** par défaut (`rag.yaml`).
- **Garde-fou de modèle :** à sa construction, `MoteurRecherche` vérifie que la table existe, qu'elle n'est pas vide, et que tous ses vecteurs ont été produits par le modèle courant. Sinon, la recherche refuse de démarrer, au lieu de renvoyer des résultats absurdes.
- **Lecture par code :** `lire_regle(code)` renvoie toutes les parties d'une règle, dans l'ordre du livre (utilisé par l'agent pour suivre les renvois).
- **Commande de diagnostic :** `uv run chercher-regles "question" [-k N] [--complet]`.

---

## 5. Agent de recherche

### Rôle

L'agent ne répond pas au joueur : il **choisit les passages** que le générateur utilisera. Cette séparation des rôles permet d'évaluer la recherche seule, de réutiliser le générateur tel quel, et de lui transmettre un contexte trié.

### Fonctionnement

- **Modèle :** Claude Sonnet 5, effort `high`, réflexion adaptative.
- **Amorce :** une première recherche est faite par le code avec la question brute. L'agent part ainsi au moins des passages de la recherche simple.
- **Outils :**
  - `rechercher_regles(requete, k)` : recherche dense avec une requête écrite par l'agent, dans le vocabulaire du livre ;
  - `lire_regle(code)` : lecture d'une règle par son code, pour suivre les renvois (`codes_cites`, affichés dans le titre de chaque passage) ;
  - `retenir_passages(etiquettes, justification)` : outil de fin, qui donne la sélection sous forme structurée.
- **Étiquettes courtes** (P1, P2…) : l'agent recopie une étiquette plutôt qu'un identifiant de 36 caractères ; un passage déjà vu n'est pas renvoyé une seconde fois.
- **Budget :** 6 tours au maximum. Un rappel est envoyé un tour avant la fin ; au dernier tour, les outils demandés ne sont plus exécutés.
- **Sélection :** entre `min_passages` (5) et `max_passages` (8) passages. Si l'agent en retient moins que le minimum, la sélection est **complétée** avec les passages qu'il a consultés sans les retenir, dans leur ordre d'arrivée, ses propres choix restant en tête.

### Fins possibles

| Fin | Situation | Passages transmis |
|---|---|---|
| `retenue` | l'agent appelle `retenir_passages` | sa sélection, complétée jusqu'au minimum |
| `retenue`, liste vide | aucun passage ne concerne la question | aucun : **abstention directe**, sans appel au générateur |
| `repli_etiquettes` | étiquettes toutes inconnues (erreur de l'agent) | passages vus, dans l'ordre |
| `repli_texte` | l'agent répond en texte au lieu de sélectionner | passages vus, dans l'ordre |
| `repli_budget` | budget de tours épuisé | passages vus, dans l'ordre |

### Points de conception

- **Pas de `tool_choice` forcé :** les modèles les plus récents refusent de forcer l'appel d'un outil. La boucle ne dépend que du prompt, de l'amorce et des replis codés.
- **Contrat `SourceRegles` :** l'agent ne dépend que de deux méthodes (`rechercher`, `lire_regle`). Un autre moteur de recherche, par exemple hybride, peut remplacer la recherche dense sans modifier l'agent.
- **Prompt** `agent_vf.md`, dont les marqueurs `{max_passages}` et `{max_tours_recherche}` sont remplacés par les valeurs de la configuration.

---

## 6. Génération

- **Modèle :** Claude Sonnet 5, effort `medium`, réflexion adaptative, `max_tokens` 4096.
  - Effort `medium` plutôt que `low` : à latence égale, les réponses incluent les précisions et cas particuliers utiles (notebook 06).
  - **Pas de paramètre `temperature`** : Sonnet 5 refuse toute valeur non par défaut.
- **Citations natives :** chaque passage est transmis en bloc `search_result` (titre : code, sous-section, pages), citations activées. Les segments de la réponse portent des citations structurées (passage cité, texte exact) : une citation ne peut désigner qu'un passage réellement fourni.
- **Granularité par paragraphe :** chaque paragraphe d'un passage est un bloc citable. Les citations ciblent ainsi le passage précis, y compris les cas particuliers.
- **Ordre du message :** les extraits d'abord, la question ensuite.
- **Prompt système** `systeme_vf.md` : réponse uniquement à partir des extraits (en justifiant l'interdiction des connaissances préalables par le risque de mélange avec une édition antérieure), abstention, couverture partielle signalée, contradictions signalées, extraits non pertinents ignorés.
- **Abstention :** phrase fixe, définie dans la configuration, dont la présence dans le prompt est vérifiée au chargement. Une réponse qui **commence** par cette phrase est comptée comme abstention.
- **Traçabilité :** chaque réponse porte le modèle, le nom du prompt, son empreinte sha256, les tokens et la durée.
- **Streaming :** le texte est affiché au fil de l'eau, puis remplacé par la version finale avec ses notes de citation.
- **Commande :** `uv run repondre-regles "question" [--agent | --no-agent]`.

---

## 7. Interface

- **Streamlit**, lancée par `uv run interface-regles` depuis n'importe quel dossier du repo.
- **Pages :** assistant (chat), informations sur le modèle (texte éditable et configuration active), documents de référence (avec le nombre de passages indexés par document).
- **Chat :** streaming, notes de citation et sources dépliables, légendes techniques (modèle, prompt, tokens, durée, part du texte citée).
- **Mode agent :** interrupteur dans la barre latérale (activé par défaut). Les étapes de l'agent s'affichent en direct, puis restent consultables dans l'historique.
- **Ressources mises en cache** (modèle d'embedding, connexion, client API) ; prompts relus à chaque question.
- **Personnalisation** sans toucher au code : textes, apparence, documents et page d'informations dans `config/ui/`, thème dans `.streamlit/config.toml`.

---

## 8. Évaluation

### Gold set

100 questions rédigées en langage de joueur, au format :

| Colonne | Contenu |
|---|---|
| `id` | identifiant |
| `question` | question telle qu'un joueur la poserait |
| `references` | liste JSON de références : `{"code": …}`, `{"page": …}` ou les deux (pour lever l'ambiguïté d'un code en double) ; vide pour une question hors corpus |
| `reponse_attendue` | réponse de référence, rédigée avec ses propres mots |
| `categorie` | type de question (`hors_corpus` pour les questions hors sujet) |

Une référence correspond à un passage si tous ses critères renseignés sont vérifiés. Le code est le critère principal, car il reste stable quand le découpage change, contrairement aux identifiants de passages.

### Métriques

- **Retrieval** (sur les passages transmis au générateur) : hit@1/3/5, hit@contexte, recall, MRR, référence citée.
- **Génération** (RAGAS 0.4, juge Claude Haiku 4.5, embeddings BGE-M3) : faithfulness, answer relevancy, context precision, context recall.
- **Comportement :** abstention juste (hors corpus), fausse abstention, coût et durée par question, tours et replis de l'agent.

### Déroulé

Le notebook 08 compare des configurations définies en dur (paramètres de recherche, de génération et de l'agent). Les résultats sont enregistrés par configuration et par question, avec une empreinte de tous les paramètres (texte des prompts compris) : seuls les manquants sont recalculés, et les questions en erreur sont relancées. Le client API réessaie automatiquement en cas de limite de débit.

### Résultats préliminaires

| Configuration | hit@5 | MRR | Faithfulness | Answer relevancy | Context precision | Context recall | Abstention juste |
|---|---|---|---|---|---|---|---|
| Baseline (top-5, sans agent) | 0,94 | 0,76 | 0,78 | 0,61 | 0,71 | 0,73 | 1,00 |
| Agent (Sonnet 5, `high`, 5 à 8 passages) | *à mesurer après relecture du gold set* | | | | | | |

**Point d'attention :** Sonnet 5 n'acceptant pas de température fixe, deux exécutions d'une même configuration peuvent différer. Un écart d'une ou deux questions n'est pas significatif.

---

## 9. Pistes explorées

| Piste | Résultat | Décision |
|---|---|---|
| **Agent en une seule étape** (recherche et réponse dans la même conversation) | Rôles mélangés, recherche impossible à évaluer seule | Remplacé par l'architecture en deux étapes |
| **Choix du modèle de l'agent** | Reformuler et explorer le livre est la partie la plus exigeante du raisonnement ; Sonnet 5 en effort `high` donne de bons résultats à l'usage | Sonnet 5 retenu ; comparaison chiffrée avec Haiku 4.5 et Opus 5.5 prévue dans l'évaluation (notebook 08) |
| **Recherche plein texte** comme outil de l'agent (notebook 09) | La recherche dense trouvait déjà le bon passage en tête pour la plupart des termes exacts ; gain net sur 1 requête sur 16, bruit sur les règles longues | Non retenue ; à réévaluer si l'analyse des échecs le justifie |
| **Recherche hybride avec fusion (RRF)** | Analysée : faible marge sur la baseline (hit@5 de 0,94), risque de dégradation | Non implémentée |

---

## 10. Hors périmètre (version 2)

- **Historique de conversation**, avec reformulation des questions de suite par l'agent.
- **Règles d'armée** : nouvelle source dans la même table, et raisonnement sur l'articulation entre règle générale et dérogation.
- **Conteneurisation de l'application** (image allégée sans Docling, torch CPU, poids du modèle intégrés, dump de la base) et **intégration continue** (GitHub Actions).
- **API** (FastAPI), si un usage hors interface le justifie.
- **Recherche hybride ou reranking**, uniquement sur preuve de gain par l'évaluation.

---

## Journal des écarts avec la spécification initiale

| Spécification initiale | Version 1 | Raison |
|---|---|---|
| Overlap d'environ 50 tokens | Pas de chevauchement (sauf filet de sécurité) | Le préfixe de chemin porte le contexte de chaque passage |
| FlagEmbedding ou sentence-transformers | sentence-transformers, derrière une interface | Bibliothèque mieux maintenue ; FlagEmbedding reste possible pour la sortie sparse |
| Table `chunks`, noms anglais | Table `chunks_w40k`, noms français du modèle `Chunk` | Passage direct du JSONL au SQL ; table unique pour toutes les sources |
| Température basse (≈ 0) | Paramètre non envoyé | Refusé par Claude Sonnet 5 ; l'ancrage repose sur le prompt et les citations |
| Citations par le texte (chapitre, section, page) | Citations natives `search_result`, par paragraphe | Citations structurées et vérifiables par construction |
| Sans agent, sans tool use | Agent de recherche en deux étapes | Gain net observé sur les questions mal formulées ou à exceptions |
| Interface en CLI uniquement | CLI et interface Streamlit | Démonstration du fonctionnement, notamment de l'agent |
| Gold set de 20 à 40 questions, JSONL | 100 questions, fichier tableur, références par code et/ou page | Marge d'incertitude plus faible ; références robustes aux changements de découpage |
| RAGAS (sans précision de version) | RAGAS 0.4, `langchain-community` épinglé sous 0.4 | Incompatibilité d'import entre RAGAS 0.4.3 et langchain-community 0.4 |
| Configuration centralisée | YAML par module, validés par Pydantic, config RAG unique | Une seule section d'embedding partagée garantit le même modèle pour passages et questions |

## Questions ouvertes de la spécification initiale : réponses

- **Dimension d'embedding :** 1024, confirmée sur le modèle chargé.
- **Longueur maximale et taille des passages :** 568 tokens au plus pour 8192 autorisés ; contrôle explicite contre la troncature.
- **Un modèle généraliste suffit-il ?** Sur la baseline, hit@5 de 0,94 : oui pour retrouver les passages. Les difficultés restantes tiennent au vocabulaire des joueurs et aux exceptions, traitées par l'agent.
- **Taille des passages, overlap, k :** 400 / 600 tokens, sans overlap, k = 5 ; en mode agent, de 5 à 8 passages transmis.
