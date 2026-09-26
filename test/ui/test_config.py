"""Tests de la configuration de l'interface (config/ui/)."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from assistant_regles.ingest.config import trouver_racine
from assistant_regles.ui.config import charger_config_ui


@pytest.fixture
def dossier_copie(tmp_path) -> Path:
    """Copie de config/ui/ dans <tmp>/config/ui, modifiable par les tests."""
    dossier = tmp_path / "config" / "ui"
    shutil.copytree(trouver_racine() / "config" / "ui", dossier)
    return dossier


def _modifier(dossier: Path, fichier: str, modification) -> None:
    chemin = dossier / fichier
    contenu = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    modification(contenu)
    chemin.write_text(yaml.safe_dump(contenu, allow_unicode=True), encoding="utf-8")


def test_config_du_repo_valide():
    config = charger_config_ui()
    assert config.textes.application.titre
    assert config.documents and config.informations_modele.strip()


def test_image_relative_a_la_racine(dossier_copie):
    config = charger_config_ui(dossier_copie)
    assert config.chemin_image_fond == dossier_copie.parent.parent / "config/ui/images/fond.jpg"


@pytest.mark.parametrize(
    ("fichier", "modification"),
    [
        ("textes.yaml", lambda c: c["chat"].update(placeholdr="faute de frappe")),
        ("textes.yaml", lambda c: c["chat"].pop("accueil")),
        ("ui.yaml", lambda c: c["apparence"].update(opacite_image=1.5)),
        ("ui.yaml", lambda c: c["apparence"].update(couleur_accent="rouge")),
        ("documents.yaml", lambda c: c["documents"][0].update(lien="www.sans-protocole.com")),
        ("textes.yaml", lambda c: c["pied_de_page"].update(github="github.com/sans-protocole")),
    ],
)
def test_configuration_invalide_refusee(dossier_copie, fichier, modification):
    _modifier(dossier_copie, fichier, modification)
    with pytest.raises(ValidationError):
        charger_config_ui(dossier_copie)
