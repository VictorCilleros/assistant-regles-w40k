"""Chargement et validation de la configuration de l'indexation.

Un fichier YAML dans ``config/rag/`` :

- ``indexation.yaml`` : modèle d'embedding et paramètres d'exécution (la base
  vectorielle complétera ce fichier à l'étape suivante).

Les modèles refusent les clés inconnues (``extra="forbid"``) : une faute de
frappe dans le YAML fait échouer le chargement au lieu d'être ignorée en silence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from assistant_regles.ingest.config import trouver_racine

FICHIER_INDEXATION = "indexation.yaml"
DOSSIER_CONFIG_RELATIF = Path("config") / "rag"


class ParamsEmbeddings(BaseModel):
    """Modèle d'embedding et paramètres d'exécution."""

    model_config = ConfigDict(extra="forbid")

    modele: str = "BAAI/bge-m3"
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")  # hash de commit Hugging Face
    dimension: int = Field(default=1024, gt=0)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    fp16: bool = True
    batch_size: int = Field(default=32, gt=0)


class ConfigIndexation(BaseModel):
    """Contenu de indexation.yaml."""

    model_config = ConfigDict(extra="forbid")

    embeddings: ParamsEmbeddings


def _lire_yaml(chemin: Path) -> dict:
    """Lit un fichier YAML en dictionnaire."""
    with chemin.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def charger_config(dossier_config: Path | None = None) -> ConfigIndexation:
    """Charge et valide la configuration d'indexation.

    Args:
        dossier_config: dossier contenant indexation.yaml
            (défaut : <racine>/config/rag).

    Returns:
        Configuration validée.

    Raises:
        FileNotFoundError: indexation.yaml est absent.
        pydantic.ValidationError: la configuration est invalide.
    """
    if dossier_config is None:
        dossier_config = trouver_racine() / DOSSIER_CONFIG_RELATIF
    return ConfigIndexation.model_validate(_lire_yaml(dossier_config / FICHIER_INDEXATION))
