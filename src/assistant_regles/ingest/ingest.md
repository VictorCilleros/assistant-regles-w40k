# Référence : format de sortie de la pipeline d'ingestion

## Fichier
- `data/interim/chunks.jsonl` : un chunk JSON par ligne, en UTF-8 non échappé
  (les caractères « ½ », « " », « « » » apparaissent tels quels).
- Relecture typée : `from assistant_regles.ingest.chunk import lire_jsonl, Chunk`
  -> `list[Chunk]`, validée par Pydantic.
- Environ 200 chunks, 600 tokens BGE-M3 au maximum chacun, médiane autour de 150.
- Artefact d'audit à côté : `data/interim/elements.parquet`. Il contient tous les
  éléments Docling, y compris les exclus, avec `texte_brut`, `texte`,
  `motif_exclusion` et `a_relire`. Il sert au diagnostic, pas à l'embedding.

## Exemple de chunk (texte fictif)
{"id": "3f2a…", "texte": "RÈGLES ÉLÉMENTAIRES > 03 Mouvement > 03.01 DÉPLACER DES UNITÉS\n\n### FINIR UN MOUVEMENT\n…\n- …",
 "chapitre": "RÈGLES ÉLÉMENTAIRES", "section_num": "03", "section_titre": "Mouvement",
 "code": "03.01", "sous_section": "DÉPLACER DES UNITÉS", "sous_parties": ["FINIR UN MOUVEMENT"],
 "page_debut": 12, "page_fin": 13, "type_contenu": "regle", "edition": "11e",
 "source": "livre_regles_principal", "codes_cites": ["03.03"], "partie": 1, "nb_parties": 2,
 "nb_tokens": 412, "hors_budget": false, "ordres": [210, 211, 212]}

## Champs : contenu et usage
| Champ | Contenu | Pourquoi / usage aval |
|---|---|---|
| `id` | uuid5 déterministe : `uuid5(uuid5(NAMESPACE_URL, "assistant-regles-w40k"), f"{source}\|{ordres[0]}\|{ordres[-1]}")` | Clé primaire. Stable d'une exécution à l'autre tant que le parsing Docling ne change pas : upsert idempotent, résultats d'éval comparables |
| `texte` | Texte à **embarquer tel quel** (préfixe compris, voir « Transformations ») | Entrée de BGE-M3 et contexte envoyé au LLM |
| `chapitre` | Titre du chapitre, en majuscules | Filtrage et affichage |
| `section_num` | « 01 » à « 24 », ou `null` (Appendices) | Filtrage (`WHERE section_num = '10'`) |
| `section_titre` | Titre de la section (ex. « Phase de tir ») | Affichage, citation |
| `code` | « XX.YY », ou `null` (introductions de section, Appendices) | **Identifiant de citation** (« voir 03.03 ») et **clé du gold set** : hit@k = un chunk retrouvé porte le code attendu |
| `sous_section` | Titre de la sous-section codée | Affichage, citation |
| `sous_parties` | Titres des sous-parties contenues, dans l'ordre | Traçabilité d'un découpage, affichage |
| `page_debut` / `page_fin` | Pages Docling (= pages imprimées pour ce livre) | Citation « p. 42 » vérifiable par l'utilisateur |
| `type_contenu` | `table` si le chunk est un tableau seul, sinon `regle` | Filtrage ; `exemple` n'est pas encore distingué |
| `edition` / `source` | « 11e » / « livre_regles_principal » | Multi-sources à venir (codex d'armée) : filtrage et citation |
| `codes_cites` | Codes « XX.YY » mentionnés dans le texte, hors le code du chunk lui-même | Renvois entre règles : l'agent (jalon 3) pourra les suivre. **Pas** mis dans le texte embarqué, pour ne pas polluer le vecteur |
| `partie` / `nb_parties` | Rang du chunk dans sa sous-section (1 sur 1 si non découpée) | Reconstituer une règle découpée, diagnostic |
| `nb_tokens` | Taille de `texte` en tokens BGE-M3 (sans tokens spéciaux) | Contrôle du budget, dimensionnement du contexte LLM |
| `hors_budget` | `true` si > 600 tokens (élément seul indivisible) | Alerte ; aucun cas sur ce livre |
| `ordres` | Indices des éléments Docling du chunk | Traçabilité vers `elements.parquet` et le cache Docling |

## Transformations appliquées au texte d'un chunk
1. **Préfixe de contexte** en première ligne : « Chapitre > NN Section > NN.NN Titre »
   (les niveaux absents sont omis). Il est inclus dans l'embedding et dans le budget
   de tokens.
2. **Titres de sous-parties réinsérés** en `### TITRE`. Ils avaient été consommés
   pendant l'extraction ; sans eux, le chunk de « Discrétion » ne contiendrait pas
   le mot « DISCRÉTION ».
3. **Tableaux** en Markdown, séparés par des lignes vides, jamais coupés.
   **Listes** avec `- ` (puces ▪ ▫ • ● converties ; ► conservé, il marque des renvois).
4. **Normalisation sans NFKC** : caractères de contrôle et de format supprimés
   (ex. `\x08` issu du PDF), ligatures décomposées, apostrophes courbes -> `'`,
   guillemets anglais et double prime -> `"`, espaces réduites. Préservés :
   `½`, les pouces `"`, `+`, `×`, `« »`, les mots-clés en majuscules.
5. **Exclus** (absents des chunks, mais visibles dans elements.parquet) : pages
   liminaires, introduction, index, ouvertures de chapitre, numéros de page ou de
   section, coûts « 1PC » isolés, renvois « VOIR AUSSI » courts, étiquettes de
   schémas, lore des stratagèmes, doublons répétés sur une autre page.

## Règles de découpage
- Unité = sous-section codée (ou introduction de section sans code).
- Si elle fait 600 tokens ou moins : un seul chunk.
- Au-delà : regroupement de **sous-parties contiguës** jusqu'à environ 400 tokens,
  sans chevauchement (le contexte est porté par le préfixe).
- Filet de sécurité : découpage par paragraphes avec chevauchement d'un élément
  entier, uniquement si une sous-partie seule dépasse 600 tokens (jamais le cas ici).

## Limites connues (à garder en tête pour l'éval)
- Les encadrés latéraux (« Qu'est-ce que la cohésion ? ») peuvent être rattachés à la
  sous-section voisine : la section est juste, la sous-section est approximative.
- 15.11 existe deux fois (une démo en p. 55, la règle en p. 57), avec le même code.
- Quelques fragments de paragraphes coupés par Docling ; ils restent dans le bon chunk.
- Le coût en PC des stratagèmes n'est pas encore une métadonnée.

## Pour l'étape embeddings + pgvector
- On embarque `texte` tel quel, sans ajout.
- Colonnes SQL prévues : tous les champs ci-dessus (listes en `text[]` ou `jsonb`,
  à trancher), plus `embedding vector(1024)`.
- Métadonnées **candidates** à ajouter, à discuter plutôt qu'à imposer :
  - nom et version du modèle d'embedding, pour détecter les vecteurs périmés ;
  - hash du texte, pour ne recalculer que les chunks modifiés ;
  - date d'ingestion ;
  - plus tard : coût PC des stratagèmes, `type_contenu = exemple`, colonne
    `tsvector` pour la recherche hybride.
- La spec utilisait des noms anglais (`chapter`, `page_start`…). On garde les noms
  français du modèle `Chunk`, pour que le passage JSONL -> SQL soit direct.