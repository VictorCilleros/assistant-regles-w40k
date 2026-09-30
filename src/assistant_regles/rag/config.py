"""Chargement et validation de la configuration RAG.

Un fichier YAML unique dans ``config/rag/`` :

- ``rag.yaml`` : section ``embeddings`` (modèle d'embedding, partagé par
  l'indexation et la recherche), section ``recherche`` (paramètres du top-k)
  section ``generation`` (appel à Claude et prompt système) et section ``agent``
  (agent de recherche qui choisit les passages avant la génération).

Un seul fichier pour les deux usages garantit que chunks et questions sont
encodés par le même modèle.

Les modèles refusent les clés inconnues (``extra="forbid"``) : une faute de
frappe dans le YAML fait échouer le chargement au lieu d'être ignorée en silence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class ParamsGeneration(BaseModel):
    """Paramètres de la génération par Claude."""

    model_config = ConfigDict(extra="forbid")

    modele: str = "claude-sonnet-5"
    max_tokens: int = Field(default=4096, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    reflexion: bool = True  # False : thinking désactivé ; True : adaptatif (défaut du modèle)
    granularite: Literal["chunk", "paragraphe"] = "paragraphe"
    prompt_systeme: str = Field(default="systeme_vf.md", pattern=r"^[\w.-]+\.md$")
    phrase_abstention: str = Field(
        default="Je ne trouve pas la réponse dans les extraits du livre de règles fournis.",
        min_length=10,
    )


class ParamsAgent(BaseModel):
    """Paramètres de l'agent de recherche (modèle, budget, prompt)."""

    model_config = ConfigDict(extra="forbid")

    actif: bool = True
    modele: str = "claude-sonnet-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "high"  # None : non envoyé
    max_tokens: int = Field(default=16_000, gt=0)
    max_tours: int = Field(default=6, ge=2)  # au moins un tour de recherche et un pour conclure
    max_passages: int = Field(default=8, gt=0)
    min_passages: int = Field(default=5, ge=0)  # complément avec les passages vus ; 0 = aucun
    k: int = Field(default=5, gt=0)
    amorce: bool = True
    prompt: str = Field(default="agent_vf.md", pattern=r"^[\w.-]+\.md$")

    @model_validator(mode="after")
    def verifier_bornes(self) -> ParamsAgent:
        """Le minimum de passages ne peut pas dépasser le maximum."""
        if self.min_passages > self.max_passages:
            raise ValueError("min_passages doit être inférieur ou égal à max_passages")
        return self


class ConfigRag(BaseModel):
    """Contenu de rag.yaml."""

    model_config = ConfigDict(extra="forbid")

    embeddings: ParamsEmbeddings
    recherche: ParamsRecherche = Field(default_factory=ParamsRecherche)
    generation: ParamsGeneration = Field(default_factory=ParamsGeneration)
    agent: ParamsAgent = Field(default_factory=ParamsAgent)


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
