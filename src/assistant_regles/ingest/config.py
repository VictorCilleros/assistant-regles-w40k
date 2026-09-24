"""Chargement et validation de la configuration d'ingestion.

Deux fichiers YAML dans ``config/ingest/`` :

- ``pipeline.yaml`` : paramètres d'exécution (chemins, budgets de tokens, seuils) ;
- ``structure_livre.yaml`` : chapitres, sections et pages du livre traité.

La validation (Pydantic) fait échouer le pipeline dès le chargement si la
configuration est incohérente (sections hors de leur chapitre, chapitres qui se
chevauchent…), plutôt que de ranger silencieusement du contenu au mauvais endroit.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

FICHIER_PIPELINE = "pipeline.yaml"
FICHIER_STRUCTURE = "structure_livre.yaml"
DOSSIER_CONFIG_RELATIF = Path("config") / "ingest"


# --------------------------------------------------------------------------- #
# Structure du livre
# --------------------------------------------------------------------------- #
class Section(BaseModel):
    """Section du livre (numérotée globalement « 01 », « 02 »… ou non numérotée)."""

    num: str | None = Field(default=None, pattern=r"^\d{2}$")
    titre: str
    page_debut: int = Field(ge=1)


class Chapitre(BaseModel):
    """Chapitre du livre : intervalle de pages contenant des sections."""

    titre: str
    page_debut: int = Field(ge=1)
    page_fin: int = Field(ge=1)
    sections: list[Section] = Field(default_factory=list)

    @model_validator(mode="after")
    def verifier_sections(self) -> Chapitre:
        """Vérifie l'intervalle de pages et que les sections sont ordonnées et incluses."""
        if self.page_fin < self.page_debut:
            raise ValueError(f"Chapitre « {self.titre} » : page_fin < page_debut")
        pages = [s.page_debut for s in self.sections]
        if pages != sorted(pages):
            raise ValueError(f"Chapitre « {self.titre} » : sections non triées par page")
        for s in self.sections:
            if not self.page_debut <= s.page_debut <= self.page_fin:
                raise ValueError(
                    f"Section « {s.titre} » (p. {s.page_debut}) hors du chapitre "
                    f"« {self.titre} » (p. {self.page_debut}-{self.page_fin})"
                )
        return self


class StructureLivre(BaseModel):
    """Structure complète d'un livre : chapitres ordonnés et disjoints."""

    chapitres: list[Chapitre]

    @model_validator(mode="after")
    def verifier_chapitres(self) -> StructureLivre:
        """Vérifie que les chapitres sont triés et ne se chevauchent pas."""
        for precedent, suivant in pairwise(self.chapitres):
            if suivant.page_debut <= precedent.page_fin:
                raise ValueError(
                    f"Chapitres qui se chevauchent : « {precedent.titre} » "
                    f"(fin p. {precedent.page_fin}) et « {suivant.titre} » "
                    f"(début p. {suivant.page_debut})"
                )
        return self


# --------------------------------------------------------------------------- #
# Paramètres du pipeline
# --------------------------------------------------------------------------- #
class Chemins(BaseModel):
    """Chemins des entrées et artefacts (relatifs à la racine du repo ou absolus)."""

    pdf: Path
    cache_docling: Path
    elements: Path
    chunks: Path


class ParamsDocument(BaseModel):
    """Identification du document source, recopiée dans les métadonnées des chunks."""

    source: str
    edition: str


class ParamsDocling(BaseModel):
    """Options de conversion Docling."""

    ocr: bool = False
    structure_tableaux: bool = True


class ParamsNettoyage(BaseModel):
    """Seuils et zones utilisés par l'annotation de nettoyage."""

    zones_hors_regles: list[str] = Field(default_factory=list)
    longueur_max_etiquette: int = Field(default=25, gt=0)
    longueur_max_renvoi: int = Field(default=50, gt=0)
    seuil_a_relire: int = Field(default=15, ge=0)


class ParamsChunking(BaseModel):
    """Budget de découpage, en tokens du tokenizer d'embedding."""

    tokenizer: str = "BAAI/bge-m3"
    cible_tokens: int = Field(default=400, gt=0)
    max_tokens: int = Field(default=600, gt=0)

    @model_validator(mode="after")
    def verifier_budget(self) -> ParamsChunking:
        """La cible de regroupement ne peut pas dépasser le maximum."""
        if self.cible_tokens > self.max_tokens:
            raise ValueError("cible_tokens doit être inférieur ou égal à max_tokens")
        return self


class ConfigPipeline(BaseModel):
    """Contenu de pipeline.yaml."""

    chemins: Chemins
    document: ParamsDocument
    docling: ParamsDocling = Field(default_factory=ParamsDocling)
    nettoyage: ParamsNettoyage = Field(default_factory=ParamsNettoyage)
    chunking: ParamsChunking = Field(default_factory=ParamsChunking)


class ConfigIngestion(BaseModel):
    """Configuration complète : paramètres + structure + racine du repo."""

    racine: Path
    pipeline: ConfigPipeline
    structure: StructureLivre

    def resoudre(self, chemin: Path) -> Path:
        """Rend un chemin de la config absolu (relatif à la racine du repo)."""
        return chemin if chemin.is_absolute() else self.racine / chemin

    @property
    def chemin_pdf(self) -> Path:
        return self.resoudre(self.pipeline.chemins.pdf)

    @property
    def chemin_cache_docling(self) -> Path:
        return self.resoudre(self.pipeline.chemins.cache_docling)

    @property
    def chemin_elements(self) -> Path:
        return self.resoudre(self.pipeline.chemins.elements)

    @property
    def chemin_chunks(self) -> Path:
        return self.resoudre(self.pipeline.chemins.chunks)


# --------------------------------------------------------------------------- #
# Chargement
# --------------------------------------------------------------------------- #
def trouver_racine(depart: Path | None = None) -> Path:
    """Remonte l'arborescence jusqu'au dossier contenant pyproject.toml.

    Permet de lancer le pipeline depuis la racine, depuis notebooks/ ou depuis test/.

    Args:
        depart: dossier de départ (défaut : dossier courant).

    Returns:
        Chemin absolu de la racine du repo.

    Raises:
        FileNotFoundError: aucun pyproject.toml trouvé en remontant.
    """
    courant = (depart or Path.cwd()).resolve()
    for dossier in (courant, *courant.parents):
        if (dossier / "pyproject.toml").exists():
            return dossier
    raise FileNotFoundError(f"Aucun pyproject.toml trouvé au-dessus de {courant}")


def _lire_yaml(chemin: Path) -> dict:
    """Lit un fichier YAML en dictionnaire."""
    with chemin.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def charger_config(dossier_config: Path | None = None) -> ConfigIngestion:
    """Charge et valide la configuration d'ingestion.

    Args:
        dossier_config: dossier contenant pipeline.yaml et structure_livre.yaml
            (défaut : <racine>/config/ingest). La racine du repo est déduite
            comme le parent de config/.

    Returns:
        Configuration validée.

    Raises:
        FileNotFoundError: un des fichiers de configuration est absent.
        pydantic.ValidationError: la configuration est incohérente.
    """
    if dossier_config is None:
        racine = trouver_racine()
        dossier_config = racine / DOSSIER_CONFIG_RELATIF
    else:
        dossier_config = dossier_config.resolve()
        racine = dossier_config.parent.parent  # <racine>/config/ingest

    pipeline = ConfigPipeline.model_validate(_lire_yaml(dossier_config / FICHIER_PIPELINE))
    structure = StructureLivre.model_validate(_lire_yaml(dossier_config / FICHIER_STRUCTURE))
    return ConfigIngestion(racine=racine, pipeline=pipeline, structure=structure)
