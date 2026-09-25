"""Chargement et validation de la configuration RAG.

Un fichier YAML unique dans ``config/rag/`` :

- ``rag.yaml`` : section ``embeddings`` (modèle d'embedding, partagé par
  l'indexation et la recherche) et section ``recherche`` (paramètres du top-k).

Un seul fichier pour les deux usages garantit que chunks et questions sont
encodés par le même modèle.

Les modèles refusent les clés inconnues (``extra="forbid"``) : une faute de
frappe dans le YAML fait échouer le chargement au lieu d'être ignorée en silence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from assistant_regles.ingest.config import trouver_racine

FICHIER_RAG = "rag.yaml"
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


class ParamsRecherche(BaseModel):
    """Paramètres de la recherche top-k."""

    model_config = ConfigDict(extra="forbid")

    k: int = Field(default=5, gt=0)


class ConfigRag(BaseModel):
    """Contenu de rag.yaml."""

    model_config = ConfigDict(extra="forbid")

    embeddings: ParamsEmbeddings
    recherche: ParamsRecherche = Field(default_factory=ParamsRecherche)


def _lire_yaml(chemin: Path) -> dict:
    """Lit un fichier YAML en dictionnaire."""
    with chemin.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def charger_config(dossier_config: Path | None = None) -> ConfigRag:
    """Charge et valide la configuration RAG.

    Args:
        dossier_config: dossier contenant rag.yaml (défaut : <racine>/config/rag).

    Returns:
        Configuration validée.

    Raises:
        FileNotFoundError: rag.yaml est absent.
        pydantic.ValidationError: la configuration est invalide.
    """
    if dossier_config is None:
        dossier_config = trouver_racine() / DOSSIER_CONFIG_RELATIF
    return ConfigRag.model_validate(_lire_yaml(dossier_config / FICHIER_RAG))
