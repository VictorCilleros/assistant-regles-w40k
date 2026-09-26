"""Chargement et validation de la configuration de l'interface.

Fichiers dans ``config/ui/`` :

- ``ui.yaml`` : apparence (image de fond, voile, couleur d'accent, avatars) ;
- ``textes.yaml`` : tous les textes affichés ;
- ``documents.yaml`` : registre des documents du corpus ;
- ``informations_modele.md`` : texte de la page « Informations sur le modèle ».

Les couleurs de base et les polices sont dans ``.streamlit/config.toml``
(thème natif de Streamlit). Comme ailleurs dans le projet, les clés inconnues
sont refusées : une faute de frappe dans un YAML est signalée au lancement.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from assistant_regles.ingest.config import trouver_racine

DOSSIER_CONFIG_RELATIF = Path("config") / "ui"
COULEUR_HEX = r"^#[0-9A-Fa-f]{6}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# ui.yaml
# --------------------------------------------------------------------------- #
class Apparence(_Strict):
    """Réglages visuels non couverts par le thème Streamlit."""

    image_fond: Path | None = None
    opacite_image: float = Field(default=0.18, ge=0, le=1)
    couleur_accent: str = Field(default="#8B1A1A", pattern=COULEUR_HEX)
    avatar_assistant: str = ":material/shield:"
    avatar_utilisateur: str = ":material/person:"


class FichierUi(_Strict):
    apparence: Apparence = Field(default_factory=Apparence)


# --------------------------------------------------------------------------- #
# textes.yaml
# --------------------------------------------------------------------------- #
class TextesApplication(_Strict):
    titre: str
    icone: str
    sous_titre: str


class TextesNavigation(_Strict):
    assistant: str
    modele: str
    documents: str


class TextesChat(_Strict):
    accueil: str
    placeholder: str
    recherche_en_cours: str
    nouvelle_conversation: str
    titre_sources: str
    erreur: str
    details_erreur: str


class TextesModele(_Strict):
    titre_configuration: str
    intro_configuration: str


class TextesDocuments(_Strict):
    titre: str
    introduction: str
    bouton_lien: str
    libelle_chunks: str
    base_indisponible: str


URL_OU_VIDE = r"^(https?://\S+)?$"  # sans « https:// », le lien serait relatif à l'application


class PiedDePage(_Strict):
    nom: str
    email: str = ""
    github: str = Field(default="", pattern=URL_OU_VIDE)
    linkedin: str = Field(default="", pattern=URL_OU_VIDE)
    mention: str = ""


class Textes(_Strict):
    application: TextesApplication
    navigation: TextesNavigation
    chat: TextesChat
    modele: TextesModele
    documents: TextesDocuments
    pied_de_page: PiedDePage


# --------------------------------------------------------------------------- #
# documents.yaml
# --------------------------------------------------------------------------- #
class Document(_Strict):
    """Un document du corpus, tel qu'affiché dans la page dédiée."""

    nom: str
    source: str       # doit correspondre à la colonne `source` des chunks
    edition: str      # idem pour `edition`
    version: str = ""
    langue: str = ""
    editeur: str = ""
    lien: str = Field(pattern=r"^https?://")
    description: str = ""


class FichierDocuments(_Strict):
    documents: list[Document]


# --------------------------------------------------------------------------- #
# Ensemble
# --------------------------------------------------------------------------- #
class ConfigUi(_Strict):
    """Configuration complète de l'interface."""

    racine: Path
    apparence: Apparence
    textes: Textes
    documents: list[Document]
    informations_modele: str  # contenu Markdown de la page d'informations

    @property
    def chemin_image_fond(self) -> Path | None:
        """Chemin absolu de l'image de fond, ou None si non configurée."""
        image = self.apparence.image_fond
        if image is None:
            return None
        return image if image.is_absolute() else self.racine / image


def _lire_yaml(chemin: Path) -> dict:
    """Lit un fichier YAML en dictionnaire."""
    with chemin.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def charger_config_ui(dossier_config: Path | None = None) -> ConfigUi:
    """Charge et valide la configuration de l'interface.

    Args:
        dossier_config: dossier des fichiers (défaut : <racine>/config/ui).
            La racine du repo est alors son grand-parent.

    Raises:
        FileNotFoundError: un fichier est absent.
        pydantic.ValidationError: un fichier est invalide.
    """
    if dossier_config is None:
        racine = trouver_racine()
        dossier_config = racine / DOSSIER_CONFIG_RELATIF
    else:
        dossier_config = dossier_config.resolve()
        racine = dossier_config.parent.parent

    ui = FichierUi.model_validate(_lire_yaml(dossier_config / "ui.yaml"))
    return ConfigUi(
        racine=racine,
        apparence=ui.apparence,
        textes=Textes.model_validate(_lire_yaml(dossier_config / "textes.yaml")),
        documents=FichierDocuments.model_validate(_lire_yaml(dossier_config / "documents.yaml")).documents,
        informations_modele=(dossier_config / "informations_modele.md").read_text(encoding="utf-8"),
    )
