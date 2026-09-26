"""Ressources partagées entre les pages, créées une fois par processus.

``st.cache_resource`` garde en mémoire les objets coûteux (modèle d'embedding,
connexion, client API) d'une question à l'autre et d'une page à l'autre. Sans
ce cache, Streamlit réexécutant le script à chaque interaction, BGE-M3 serait
rechargé à chaque question.

Le prompt système n'est **pas** mis en cache : il est relu à chaque question,
pour que les modifications de ``systeme_vf.md`` s'appliquent sans redémarrer.

Limite assumée (usage local, un seul utilisateur) : une seule connexion
PostgreSQL est partagée. Pour plusieurs utilisateurs simultanés, il faudrait un
pool de connexions (``psycopg_pool``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st

from assistant_regles.ui.style import image_en_data_uri

if TYPE_CHECKING:
    import psycopg

    from assistant_regles.rag.config import ConfigRag
    from assistant_regles.rag.generation import Generateur
    from assistant_regles.rag.recherche import MoteurRecherche


@st.cache_resource(show_spinner=False)
def connexion() -> psycopg.Connection:
    """Connexion PostgreSQL (variables PG* du .env)."""
    import psycopg

    return psycopg.connect(autocommit=True)


@st.cache_resource(show_spinner="Chargement du modèle d'embedding…")
def moteur_recherche() -> MoteurRecherche:
    """Moteur de recherche : encodeur BGE-M3 chargé une seule fois, base vérifiée."""
    from assistant_regles.rag.config import charger_config
    from assistant_regles.rag.embeddings import EncodeurBGEM3
    from assistant_regles.rag.recherche import MoteurRecherche

    return MoteurRecherche(EncodeurBGEM3.charger(charger_config().embeddings), connexion())


@st.cache_resource(show_spinner=False)
def client_anthropic():
    """Client de l'API Anthropic (clé lue dans le .env)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY absente : la renseigner dans le .env")
    import anthropic

    return anthropic.Anthropic()


def generateur(config_rag: ConfigRag) -> Generateur:
    """Générateur avec le prompt système relu à chaque appel (pas de cache)."""
    from assistant_regles.rag.generation import Generateur, charger_prompt

    g = config_rag.generation
    return Generateur(client_anthropic(), g, charger_prompt(g.prompt_systeme, g.phrase_abstention))


def compter_chunks() -> dict[tuple[str, str], int]:
    """Nombre de passages indexés par (source, édition)."""
    from assistant_regles.rag.store import TABLE

    lignes = connexion().execute(f"SELECT source, edition, count(*) FROM {TABLE} GROUP BY 1, 2").fetchall()
    return {(source, edition): nb for source, edition, nb in lignes}


def reinitialiser() -> None:
    """Oublie la connexion et le moteur (après une perte de connexion à la base)."""
    connexion.clear()
    moteur_recherche.clear()


@st.cache_data(show_spinner=False)
def _image_encodee(chemin: str, date_modification: float) -> str | None:
    return image_en_data_uri(Path(chemin))


def image_fond(chemin: Path | None) -> str | None:
    """Image de fond encodée, mise en cache tant que le fichier ne change pas."""
    if chemin is None or not chemin.is_file():
        return image_en_data_uri(chemin)  # None, avec un avertissement si le chemin est faux
    return _image_encodee(str(chemin), chemin.stat().st_mtime)
