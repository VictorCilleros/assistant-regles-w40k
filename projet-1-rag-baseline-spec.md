# Projet 1 — Spécification technique : RAG baseline (assistant règles)

## Objectif & périmètre

**Objectif :** un RAG **simple** — recherche dense uniquement, **sans agent, sans hybrid search, sans reranking** — servant de **baseline mesurable et évaluable**.

**Corpus (v1) :** le livre de règles principal (PDF français, 100+ pages). Données traitées **en local**, non redistribuées.

**Principe directeur :** on mesure la baseline **d'abord** ; toute amélioration ultérieure (hybrid search, reranking, agent) devra être **validée par l'évaluation contre cette baseline**. On n'ajoute une brique que si elle améliore un chiffre.

---

## Récapitulatif des choix (défauts)

| Composant | Choix | Note |
|---|---|---|
| Parsing PDF | **Docling** (auto-hébergé, layout-aware, sortie Markdown) | Données locales, gère colonnes + tableaux |
| Chunking | Structurel (par section) + préfixe de contexte | Cible ≈ 400 tokens, overlap ≈ 50 ; à ajuster via l'éval |
| Embedding | **BGE-M3**, **dense uniquement**, auto-hébergé | 1024 dim, normalisé, cosinus |
| Base vectorielle | **PostgreSQL + pgvector** | Index **HNSW**, distance cosinus |
| Retrieval | Dense, top-k cosinus | k = 5 par défaut, à ajuster via l'éval |
| LLM de réponse | **Claude Sonnet** (palier Sonnet actuel, ex. `claude-sonnet-5`) | Température basse, pas de tool use |
| Évaluation | **RAGAS** + métriques retrieval maison | Juge LLM = Claude Haiku (coût) |
| Interface | Fonction Python / CLI simple | API, UI et conteneurisation = étapes ultérieures |

---

## 1. Données & pré-processing

**But :** transformer le PDF brut et bruité en unités de texte propres, découpées et enrichies de métadonnées, prêtes à embarquer. Étape qui détermine le plafond de qualité de tout le reste.

### Étapes

0. **Nature du PDF** — vérifier que le texte est sélectionnable (PDF natif → extraction directe). Sinon, prévoir une couche d'OCR.
1. **Extraction — Docling.** Parseur auto-hébergé qui comprend la mise en page (colonnes, tableaux) et sort du **Markdown** en préservant la hiérarchie de titres. On évite les extracteurs naïfs (pypdf, PyMuPDF brut) qui entrelacent les colonnes et disloquent les tableaux.
2. **Structure — chapitres / sections.** Source privilégiée : le sommaire intégré du PDF (bookmarks) ; sinon les titres Markdown produits par Docling. On construit un arbre `chapitre > section > sous-section`. En parallèle, on **classe le type de contenu** : règle / lore / tableau / exemple.
3. **Filtrage & nettoyage.**
   - Retirer : le **lore/flavor pur**, en-têtes/pieds de page répétés, pages de garde/légales, sommaire, index.
   - **Nuance :** garder les **exemples de jeu et clarifications** (ce sont des règles déguisées). « Enlever le lore » = enlever le récit, garder règles + exemples + tableaux.
   - Réparer les artefacts d'extraction (lignes coupées, mots coupés en fin de ligne). Français : UTF-8, accents, guillemets « », espaces insécables.
4. **Chunking structurel contextuel.** Découper **le long de la structure** (par section/sous-section), pas en tranches fixes aveugles. Un chunk = une unité de règle cohérente (règle + sous-points + exemple). Cible ≈ 400 tokens, overlap ≈ 50, **mais priorité aux frontières de section**. Cas spéciaux : un **tableau = un seul chunk** (Markdown, jamais coupé au milieu d'une ligne) ; un bloc de stats reste entier. On **préfixe chaque chunk par son chemin de section** (« Chapitre 3 > Combat > Tirs : … ») pour qu'il garde son contexte une fois isolé.
5. **Métadonnées.** Schéma cible par chunk :

```json
{
  "id": "uuid",
  "text": "Chapitre 3 > Combat > Tirs : ...",
  "chapter": "Combat",
  "section": "Tirs",
  "subsection": null,
  "page_start": 42,
  "page_end": 43,
  "content_type": "regle",        // regle | table | exemple
  "edition": "Xe",
  "source": "livre_regles_principal"
}
```
Le **numéro de page** est crucial : l'assistant doit pouvoir citer « p. 42 » pour que l'utilisateur vérifie.

6. **Validation.** Échantillonner 10–20 chunks et les relire : colonnes lues dans le bon ordre ? Tableaux intacts ? Le filtrage du lore n'a pas emporté de règles ? C'est cette vérification qui évite un RAG cassé en silence.

### Provenance / hygiène de repo
Le livre de règles est sous copyright : garder le **PDF et le texte extrait hors du repo public** (`.gitignore`), et prévoir un petit **extrait ou échantillon synthétique** pour la démo publique. On développe en local sur le texte complet, le GitHub public ne redistribue pas le contenu protégé. C'est aussi pour ça qu'on parse en auto-hébergé (Docling) plutôt qu'avec une API cloud.

---

## 2. Modèle d'embedding

**But :** transformer texte et requêtes en vecteurs. Le choix qui impacte le plus la qualité du retrieval.

- **Modèle : BGE-M3, auto-hébergé.** Généraliste solide et multilingue (indispensable pour le français). On l'exécute via `FlagEmbedding` (bibliothèque officielle BAAI) ou `sentence-transformers`.
- **Baseline : sortie DENSE uniquement.** (BGE-M3 produit aussi du sparse et du ColBERT — réservés à l'étape hybrid search plus tard.)
- **Dimension : 1024** (dense fixe pour BGE-M3) → colonne `vector(1024)`. À confirmer à l'implémentation.
- **Distance : cosinus.** Normaliser les vecteurs.

**À valider (une fois l'étape 1 finie) :** la longueur max d'entrée de BGE-M3 (8192 tokens) couvre-t-elle bien les chunks ? Un généraliste suffit-il sur le jargon de règles ? → réponse par l'éval de retrieval, pas a priori.

---

## 3. Base de données vectorielle

**But :** stocker vecteurs + métadonnées et servir la recherche par similarité rapidement, avec filtrage.

- **PostgreSQL + pgvector** (≥ 0.5 pour HNSW ; viser une version récente). Distance **cosinus**.
- **Index HNSW** — même si à cette échelle un scan séquentiel suffirait, on l'installe pour la **démonstration** (savoir le faire).
- **Conteneurisation = étape ultérieure** (le baseline tourne d'abord en local).

Schéma indicatif :

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chunks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    text         text NOT NULL,
    chapter      text,
    section      text,
    subsection   text,
    page_start   int,
    page_end     int,
    content_type text,          -- regle | table | exemple
    edition      text,
    source       text,
    embedding    vector(1024)   -- BGE-M3 dense, normalisé
);

-- Index HNSW (cosinus) — pour la démonstration
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

Requête de recherche (opérateur `<=>` = distance cosinus dans pgvector) :

```sql
SELECT id, text, chapter, section, page_start, page_end
FROM chunks
ORDER BY embedding <=> :query_embedding
LIMIT 5;
```
`hnsw.ef_search` (défaut 40) est ajustable côté requête pour le compromis rappel/latence.

---

## 4. LLM de réponse principal

**But :** rédiger la réponse sourcée à partir du contexte récupéré.

- **Modèle : Claude Sonnet** (palier Sonnet actuel, ex. `claude-sonnet-5`). Suffit largement pour une baseline factuelle.
- **Température basse** (≈ 0) pour rester factuel. **Pas de tool use** (baseline sans agent).
- **Ancrage (principes, prompt affiné à l'implémentation après quelques tests basiques non évalués) :** répondre **uniquement à partir du contexte fourni**, **citer** chapitre / section / page issus des métadonnées, et **dire « je ne sais pas »** si l'info n'est pas dans le contexte.

---

## 5. La chaîne RAG (implémentations)

**But :** câbler la chaîne de bout en bout, du corpus à la réponse sourcée.

- **Ingestion :** PDF → Docling → structure → nettoyage → chunking → embedding BGE-M3 (dense) → upsert dans pgvector avec métadonnées. *(C'est un pipeline : réutiliser tes conventions de code.)*
- **Retrieval :** embed de la requête (même BGE-M3) → recherche cosinus **top-k (k = 5)** dans pgvector → renvoi des k chunks + métadonnées. *(Rien d'autre : pas de hybrid, pas de rerank.)*
- **Génération :** construction du prompt (système d'ancrage + contexte balisé avec ses sources + question) → appel Claude Sonnet (température basse) → réponse avec citations.
- **Interface baseline :** une fonction Python appelable / un petit CLI suffit pour rendre la chaîne exécutable et évaluable. *(API FastAPI, UI Streamlit et Docker = étapes ultérieures.)*

Arborescence de repo (version baseline) :

```
rag-regles-baseline/
├── README.md
├── pyproject.toml
├── .env.example              # ANTHROPIC_API_KEY, connexion PG
├── .gitignore                # exclut data/ (PDF + texte extrait)
├── src/
│   ├── config.py             # settings centralisés
│   ├── ingest/
│   │   ├── parse.py          # Docling → Markdown structuré
│   │   ├── clean.py          # filtrage lore, nettoyage
│   │   └── chunk.py          # chunking structurel + métadonnées
│   ├── rag/
│   │   ├── embeddings.py     # BGE-M3 dense (FlagEmbedding)
│   │   ├── store.py          # upsert + recherche pgvector
│   │   ├── retrieve.py       # embed requête → top-k
│   │   └── generate.py       # prompt + appel Claude Sonnet
│   └── cli.py                # poser une question en ligne de commande
├── evals/
│   ├── dataset.jsonl         # jeu Q/R avec vérité terrain (section + page)
│   ├── retrieval_metrics.py  # hit-rate@k, recall@k, MRR
│   └── run_eval.py           # RAGAS + métriques retrieval → rapport
└── data/                     # PDF + texte extrait (gitignored)
```

---

## 6. Évaluation (le cœur de la baseline)

**But :** produire des chiffres de référence, avant toute optimisation.

- **Jeu d'évaluation (gold set) :** ≈ 20–40 questions avec **vérité terrain** — la réponse attendue **et** le passage/section/page censé être récupéré. Inclure des cas durs (règles proches, questions multi-sections). RAGAS peut aider à générer un premier jeu synthétique à relire.
- **Métriques de retrieval** (nécessitent le gold set) : **hit-rate@k**, **recall@k**, **MRR**. Script maison simple.
- **Métriques de génération** (via **RAGAS**) : **faithfulness** (ancrage), **answer relevance**, **context precision**, **context recall**. Juge LLM configuré sur **Claude Haiku** (coût).
- **Point d'attention spécifique :** surveiller la **faithfulness**. Claude peut connaître une partie du sujet par son pré-entraînement → une réponse peut être *correcte mais non ancrée* dans le contexte (il répond de mémoire). C'est le pattern le plus dangereux : l'éval doit le détecter.
- **Outils :** **RAGAS** pour l'expérimentation et les 4 métriques ; **DeepEval** envisageable plus tard pour intégrer l'éval en CI (tests pytest).
- **Process :** exécuter la chaîne sur le gold set → logguer requêtes/retrievals/réponses → calculer les métriques → **figer les chiffres comme baseline**.

---

## 7. Hors périmètre (étapes suivantes)

À traiter **après** la baseline mesurée, chacune validée contre elle :
- **Hybrid search** (dense + sparse de BGE-M3, combiné à la recherche plein-texte Postgres)
- **Reranking** (précision du top-k)
- **Couche agentique** (function calling, retrieval itératif, décomposition de requête)
- **API + UI** (FastAPI + Streamlit)
- **Conteneurisation** (Docker + docker-compose) et **CI** (GitHub Actions)

---

## Questions ouvertes / à valider

- **Dimension d'embedding** : 1024 (BGE-M3 dense) — à confirmer à l'implémentation.
- **Longueur max d'entrée vs taille de chunk** : à vérifier une fois l'étape 1 terminée.
- **Un généraliste (BGE-M3) suffit-il** sur le jargon de règles ? → à trancher par l'éval de retrieval.
- **Taille de chunk / overlap / k** : valeurs de départ (≈ 400 / ≈ 50 / 5) à ajuster via l'éval.
