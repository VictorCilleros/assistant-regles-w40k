# Assistant de règles — Warhammer 40 000

Assistant conversationnel qui répond à des questions précises sur un corpus de règles, en s'appuyant **uniquement** sur ce corpus et en citant ses sources.

## Le problème traité

De nombreuses organisations s'appuient sur des corpus de règles à deux niveaux : un socle général (réglementation, procédures, conditions générales) complété par des règles spécifiques qui le précisent ou y dérogent (dérogations sectorielles, avenants, procédures locales). Face à une situation concrète, l'utilisateur doit retrouver les règles applicables dans les deux niveaux, comprendre comment elles s'articulent et déterminer laquelle prévaut.

Ce projet construit un assistant qui prend en charge ce raisonnement :

- l'utilisateur décrit sa situation et son doute en langage naturel ;
- l'assistant retrouve les passages pertinents du socle général et des règles spécifiques ;
- il répond en expliquant l'articulation entre ces règles, avec citation des sources (document, page) ;
- il répond « je ne sais pas » lorsque le corpus ne permet pas de conclure, plutôt que d'inventer.

## Pourquoi Warhammer 40 000 ?

Pour rendre le projet plus concret et plus ludique qu'un cas d'école, le corpus retenu est celui du jeu de figurines Warhammer 40 000 (11e édition). Sa structure reproduit fidèlement le problème ci-dessus :

| Cas générique              | Warhammer 40 000                    |
|----------------------------|-------------------------------------|
| Réglementation générale    | Règles de base                      |
| Dérogations spécifiques    | Règles propres à une armée          |
| Situation d'un usager      | Situation de jeu décrite par un joueur |

Exemple de question visée : *« Mon unité a battu en retraite ce tour-ci, mais une règle de mon armée semble l'autoriser à agir quand même : puis-je tirer ? »*

Autre intérêt : la 11e édition est sortie en juin 2026, après la date de fin d'entraînement de nombreux LLM, qui connaissent surtout l'édition précédente. Sans recherche documentaire, un modèle risque de mélanger les deux éditions avec assurance. Ce corpus est donc un bon terrain pour mesurer l'apport du RAG et la fidélité des réponses.

## Données et propriété intellectuelle

Les documents de règles utilisés pour développer et évaluer ce projet (règles de base et règles d'armée, 11e édition, en français) ont été achetés par l'auteur. Protégés par le droit d'auteur, ils ne sont **pas** inclus dans ce dépôt, pas plus que les données qui en dérivent (découpages, embeddings, index vectoriel).

Ces documents servent uniquement à construire l'index de recherche documentaire : **aucun modèle n'est entraîné dessus**.

Pour utiliser l'assistant, fournissez vos propres exemplaires des documents (voir *Installation*).

> Projet personnel non officiel, ni affilié à ni approuvé par Games Workshop. Warhammer 40,000 est une marque de Games Workshop Limited.

## Stack

Python 3.13
[uv](https://docs.astral.sh/uv/)
SDK Anthropic (Claude)

-> La stack s'enrichira au fil du développement (FastAPI, base vectorielle, Docker, GitHub Actions).

## Installation

Prérequis : `uv` et une clé API Anthropic.

```bash
git clone git@github.com:VictorCilleros/assistant-regles-w40k.git
cd assistant-regles-w40k
uv sync
cp .env.example .env   # puis renseigner ANTHROPIC_API_KEY
```

Placez vos documents de règles dans un dossier `data/` à la racine (ignoré par Git).

## Licence

Code distribué sous licence MIT (voir [LICENSE](LICENSE)). Cette licence couvre uniquement le code : elle ne s'applique pas aux documents de règles, qui restent la propriété de leurs ayants droit.
