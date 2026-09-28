Cet assistant est un système de **RAG** (*Retrieval-Augmented Generation*) : il
recherche d'abord les passages pertinents du livre de règles, puis demande à un
modèle de langage de rédiger une réponse **uniquement** à partir de ces passages.

### 1. Préparation du corpus

Le livre de règles (PDF) est converti par **Docling**, un outil qui comprend la
mise en page (colonnes, tableaux). Le texte est ensuite découpé en **passages**
qui suivent la structure du livre : une sous-section de règle par passage, avec
son chemin (« Chapitre > Section > Sous-section ») en en-tête pour garder le
contexte. Chaque passage conserve son code de règle et ses pages.

### 2. Recherche des passages

Chaque passage est transformé en **vecteur** (1 024 nombres) par le modèle
**BGE-M3**, un modèle multilingue exécuté localement. La question est encodée de
la même façon, et les passages dont le vecteur est le plus proche (similarité
cosinus) sont retrouvés dans une base **PostgreSQL + pgvector**.

### 3. Agent de recherche (mode agent)

Les joueurs n'emploient pas toujours le vocabulaire du livre, et une règle
générale a souvent ses exceptions ailleurs. En mode agent, un premier modèle
(**Claude Sonnet 5**) reformule la question avec les termes du livre, lance
plusieurs recherches, suit les renvois entre règles, puis **sélectionne** les
passages utiles. Ses étapes s'affichent pendant la recherche. Le mode agent se
désactive dans la barre latérale.

### 4. Rédaction de la réponse

Les passages retenus sont transmis à **Claude Sonnet 5** (Anthropic) avec des
consignes strictes : répondre uniquement à partir des extraits, signaler ce qui
n'est pas couvert, et dire clairement quand la réponse n'est pas dans le livre.
Les **citations** sont fournies nativement par l'API : chaque passage cité est
un extrait réellement transmis, affiché sous la réponse.

### Limites actuelles

- Chaque question est traitée indépendamment : l'assistant ne tient pas compte
  des questions précédentes.
- La recherche est purement sémantique (pas encore de recherche hybride ni de
  reclassement des résultats).
- Le mode agent est plus lent et plus coûteux : il fait plusieurs appels au
  modèle avant de répondre.
- Seul le livre de règles de base est indexé : les codex d'armée ne sont pas
  couverts.
