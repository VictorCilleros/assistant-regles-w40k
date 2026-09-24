"""Tests du chargement et de la validation de la configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_regles.ingest.config import (
    Chapitre,
    ParamsChunking,
    Section,
    StructureLivre,
    charger_config,
    trouver_racine,
)

RACINE_REPO = Path(__file__).resolve().parents[2]


def test_config_du_repo_valide():
    """Les fichiers config/ingest/*.yaml du repo se chargent et sont cohérents."""
    config = charger_config(RACINE_REPO / "config" / "ingest")
    assert config.structure.chapitres
    assert config.chemin_pdf.is_absolute()
    assert config.chemin_pdf.suffix == ".pdf"
    assert config.pipeline.chunking.cible_tokens <= config.pipeline.chunking.max_tokens


def test_section_hors_de_son_chapitre_refusee():
    with pytest.raises(ValidationError, match="hors du chapitre"):
        Chapitre(titre="A", page_debut=6, page_fin=25,
                 sections=[Section(num="01", titre="S", page_debut=46)])


def test_sections_non_triees_refusees():
    with pytest.raises(ValidationError, match="non triées"):
        Chapitre(titre="A", page_debut=6, page_fin=25, sections=[
            Section(num="02", titre="S2", page_debut=12),
            Section(num="01", titre="S1", page_debut=8),
        ])


def test_chapitres_qui_se_chevauchent_refuses():
    with pytest.raises(ValidationError, match="chevauchent"):
        StructureLivre(chapitres=[
            Chapitre(titre="A", page_debut=6, page_fin=25),
            Chapitre(titre="B", page_debut=20, page_fin=43),
        ])


def test_numero_de_section_non_quote_refuse():
    """En YAML, `num: 01` sans guillemets devient l'entier 1 : on doit le refuser."""
    with pytest.raises(ValidationError):
        Section.model_validate({"num": 1, "titre": "S", "page_debut": 8})


def test_cible_superieure_au_max_refusee():
    with pytest.raises(ValidationError, match="cible_tokens"):
        ParamsChunking(cible_tokens=700, max_tokens=600)


def test_trouver_racine_depuis_un_sous_dossier(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    sous_dossier = tmp_path / "notebooks" / "exploration"
    sous_dossier.mkdir(parents=True)
    assert trouver_racine(sous_dossier) == tmp_path.resolve()
