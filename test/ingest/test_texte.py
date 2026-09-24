"""Tests de la normalisation de texte (titres et corps des règles)."""

from __future__ import annotations

import pytest

from assistant_regles.ingest.texte import (
    normaliser_pour_comparaison,
    normaliser_texte,
    preparer_titre,
)


@pytest.mark.parametrize(
    "texte", ['Avancez de ½" maximum.', 'Portée 24"', "5+ à la touche", "« Détruit »"]
)
def test_caracteres_du_jeu_preserves(texte):
    """½, pouces, +, guillemets français : rien ne doit être altéré (pas de NFKC)."""
    assert normaliser_texte(texte) == texte


def test_caracteres_de_controle_supprimes():
    assert normaliser_texte("[ANTI] 24.03 \x08") == "[ANTI] 24.03"
    assert normaliser_texte("déter\u00admination") == "détermination"  # trait d'union conditionnel


def test_retour_ligne_devient_espace_sans_coller_les_mots():
    assert normaliser_texte("jet de\ncharge") == "jet de charge"


def test_espaces_multiples_et_insecables():
    texte = "JOUEUR ACTIF ET\u00a0 JOUEUR  ADVERSE"
    assert normaliser_texte(texte) == "JOUEUR ACTIF ET JOUEUR ADVERSE"


@pytest.mark.parametrize("puce", ["▪", "▫", "•", "●"])
def test_puces_converties_en_tiret(puce):
    assert normaliser_texte(f"{puce}Jet de charge") == "- Jet de charge"


def test_fleche_de_renvoi_conservee():
    assert normaliser_texte("►Détruit") == "►Détruit"


def test_apostrophes_et_guillemets_harmonises():
    assert normaliser_texte("l’unité") == "l'unité"
    assert normaliser_texte("à 8” ou moins") == 'à 8" ou moins'


def test_tableau_garde_ses_retours_a_la_ligne():
    tableau = "| A  | B |\n|---|---|\n| 1\x08 |  2 |"
    assert normaliser_texte(tableau, est_tableau=True) == "| A | B |\n|---|---|\n| 1 | 2 |"


def test_preparer_titre_retire_le_retour_arriere():
    assert preparer_titre("[ANTI] 24.03 \x08") == "[ANTI] 24.03"


def test_comparaison_sans_accents_ni_casse():
    assert normaliser_pour_comparaison("RÈGLES ÉLÉMENTAIRES") == "regles elementaires"
