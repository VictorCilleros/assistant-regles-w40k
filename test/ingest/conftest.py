"""Fixtures partagées des tests d'ingestion.

Aucun test n'utilise le vrai PDF (livre sous copyright, absent du repo) :
tout repose sur des données synthétiques qui reproduisent les cas réels rencontrés.
"""

from __future__ import annotations

import pandas as pd
import pytest

from assistant_regles.ingest.config import (
    Chapitre,
    ParamsChunking,
    ParamsDocument,
    ParamsNettoyage,
    Section,
    StructureLivre,
)

ELEMENT_PAR_DEFAUT = {
    "chapitre": "RÈGLES ÉLÉMENTAIRES",
    "section_num": "01",
    "section_titre": "Concepts de base",
    "code": "01.01",
    "sous_section": "ARMÉES",
    "sous_partie": None,
    "page": 8,
    "type_contenu": "text",
    "dans_image": False,
    "texte": "Texte.",
    "codes_cites": [],
}


@pytest.fixture
def structure_mini() -> StructureLivre:
    """Mini-livre : deux chapitres, dont une partie non numérotée (Appendices)."""
    return StructureLivre(chapitres=[
        Chapitre(titre="RÈGLES ÉLÉMENTAIRES", page_debut=6, page_fin=25, sections=[
            Section(num="01", titre="Concepts de base", page_debut=8),
            Section(num="02", titre="Fiches techniques", page_debut=10),
        ]),
        Chapitre(titre="RÉFÉRENCES", page_debut=76, page_fin=89, sections=[
            Section(num="24", titre="Aptitudes de base", page_debut=78),
            Section(num=None, titre="Appendices des règles", page_debut=86),
        ]),
    ])


@pytest.fixture
def params_nettoyage() -> ParamsNettoyage:
    return ParamsNettoyage(
        zones_hors_regles=["Index des règles de base", "(ouverture de chapitre)", "introduction"]
    )


@pytest.fixture
def document() -> ParamsDocument:
    return ParamsDocument(source="livre_test", edition="11e")


@pytest.fixture
def params_chunking() -> ParamsChunking:
    """Petit budget, exprimé en mots (voir ``compter_mots``)."""
    return ParamsChunking(tokenizer="factice", cible_tokens=25, max_tokens=30)


@pytest.fixture
def compter_mots():
    """Compteur de « tokens » factice : un mot = un token. Rapide, sans téléchargement."""
    return lambda texte: len(texte.split())


@pytest.fixture
def fabriquer_elements():
    """Fabrique un DataFrame d'éléments ; chaque ligne complète les valeurs par défaut."""
    def _fabriquer(lignes: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"ordre": i, **ELEMENT_PAR_DEFAUT, **ligne} for i, ligne in enumerate(lignes)]
        )
    return _fabriquer


@pytest.fixture
def element():
    """Fabrique un élément isolé (dict) pour tester les règles une par une."""
    def _element(**champs) -> dict:
        return {"ordre": 0, **ELEMENT_PAR_DEFAUT, **champs}
    return _element
