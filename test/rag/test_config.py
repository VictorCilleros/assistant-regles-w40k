"""Tests du chargement de la configuration RAG (rag.yaml)."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from assistant_regles.rag.config import charger_config

REVISION_FACTICE = "0123456789abcdef0123456789abcdef01234567"


def _config_valide() -> dict:
    return {
        "embeddings": {
            "modele": "BAAI/bge-m3",
            "revision": REVISION_FACTICE,
            "dimension": 1024,
            "device": "auto",
            "fp16": True,
            "batch_size": 32,
        },
        "recherche": {"k": 5},
    }


def _ecrire(dossier: Path, contenu: dict) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "rag.yaml").write_text(yaml.safe_dump(contenu), encoding="utf-8")
    return dossier


def test_config_valide(tmp_path):
    config = charger_config(_ecrire(tmp_path, _config_valide()))
    assert config.embeddings.revision == REVISION_FACTICE
    assert config.embeddings.dimension == 1024
    assert config.recherche.k == 5


def test_valeurs_par_defaut(tmp_path):
    config = charger_config(_ecrire(tmp_path, {"embeddings": {"revision": REVISION_FACTICE}}))
    params = config.embeddings
    assert (params.modele, params.device, params.fp16, params.batch_size) == (
        "BAAI/bge-m3", "auto", True, 32,
    )
    assert config.recherche.k == 5  # section recherche absente : valeurs par défaut


@pytest.mark.parametrize(
    ("section", "cle", "valeur"),
    [
        ("embeddings", "revision", "main"),                   # branche, pas un commit
        ("embeddings", "revision", REVISION_FACTICE.upper()),  # un hash de commit est en minuscules
        ("embeddings", "device", "gpu"),
        ("embeddings", "batch_size", 0),
        ("embeddings", "dimension", 0),
        ("recherche", "k", 0),
    ],
)
def test_valeur_invalide_refusee(tmp_path, section, cle, valeur):
    contenu = _config_valide()
    contenu[section][cle] = valeur
    with pytest.raises(ValidationError):
        charger_config(_ecrire(tmp_path, contenu))


@pytest.mark.parametrize(
    "modifier",
    [
        lambda c: c["embeddings"].update(batchsize=16),  # faute de frappe dans une section
        lambda c: c["recherche"].update(top_k=3),
        lambda c: c.update(rechercher={"k": 3}),         # faute de frappe sur une section
    ],
)
def test_cle_inconnue_refusee(tmp_path, modifier):
    contenu = _config_valide()
    modifier(contenu)
    with pytest.raises(ValidationError):
        charger_config(_ecrire(tmp_path, contenu))


def test_fichier_absent(tmp_path):
    with pytest.raises(FileNotFoundError):
        charger_config(tmp_path)


def test_config_du_repo_valide():
    """Le fichier versionné config/rag/rag.yaml doit toujours se charger."""
    config = charger_config()
    assert config.embeddings.dimension == 1024
    assert config.recherche.k >= 1
