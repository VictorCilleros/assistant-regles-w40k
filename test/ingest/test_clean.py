"""Tests de l'annotation de nettoyage.

Chaque cas reproduit un élément réel : les faux positifs corrigés en cours de route
(renvoi long, fragment de phrase dans une image, répétitions légitimes) sont des
tests de non-régression.
"""

from __future__ import annotations

import pytest

from assistant_regles.ingest.clean import (
    annoter_nettoyage,
    contenu_conserve,
    motif_ligne,
    rapport_nettoyage,
    ressemble_a_une_etiquette,
)


# --------------------------------------------------------------------------- #
# motif_ligne : une règle à la fois
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("champs", "motif"),
    [
        ({"chapitre": "LIMINAIRE", "texte": "Dans les ténèbres…"}, "zone_hors_regles"),
        ({"section_titre": "introduction", "texte": "Scannez le code."}, "zone_hors_regles"),
        ({"texte": "-›"}, "sans_contenu"),
        ({"texte": "24", "section_num": "24", "page": 80}, "numero_section"),
        ({"texte": "57", "section_num": "15", "page": 56}, "numero_page"),   # page d'en face
        ({"texte": "1PC"}, "cout_isole"),
        ({"texte": "2PC"}, "cout_isole"),
        ({"texte": "- Transports 18.00"}, "renvoi"),
        ({"texte": "Modifier le Coût en PC", "sous_partie": "VOIR AUSSI"}, "renvoi"),
        ({"texte": "Terrain Dense", "dans_image": True}, "etiquette_image"),
        ({"texte": "DÉBUT DE LA PHASE DE TIR", "dans_image": True}, "etiquette_image"),
        ({"texte": '15"', "dans_image": True}, "etiquette_image"),
    ],
)
def test_elements_exclus(element, params_nettoyage, champs, motif):
    assert motif_ligne(element(**champs), params_nettoyage) == motif


@pytest.mark.parametrize(
    "champs",
    [
        {"texte": "+1PC"},                                   # modificateur de coût : une règle
        {"texte": 'Avancez de ½".'},                         # ½ n'est pas « sans contenu »
        {"texte": "CIBLE : Cette unité.", "dans_image": True},
        {"texte": "Chaque figurine de cette", "dans_image": True},   # fragment de phrase
        {"texte": "cette zone de terrain.", "dans_image": True},
        {"texte": "SEULE LA MORT MET FIN AU DEVOIR Les unités de meneur et d'appui ont souvent "
                  "des aptitudes", "sous_partie": "VOIR AUSSI"},      # vrai contenu dans la zone
        {"texte": "- Jet de charge"},                        # vraie liste, sans code
        {"texte": "ennemie subit 1 blessure mortelle (06.02)."},     # renvoi en fin de phrase
    ],
)
def test_elements_conserves(element, params_nettoyage, champs):
    assert motif_ligne(element(**champs), params_nettoyage) is None


def test_ressemble_a_une_etiquette():
    assert ressemble_a_une_etiquette("OPTIONS D'ÉQUIPEMENT")
    assert ressemble_a_une_etiquette("Objectif de Terrain")
    assert not ressemble_a_une_etiquette("Chaque figurine de cette")
    assert not ressemble_a_une_etiquette("cette zone de terrain.")


# --------------------------------------------------------------------------- #
# annoter_nettoyage : règles sur l'ensemble du document
# --------------------------------------------------------------------------- #
def test_normalisation_et_texte_brut_conserve(fabriquer_elements, params_nettoyage):
    df = annoter_nettoyage(fabriquer_elements([{"texte": "▪Jet de charge (voir 11.02)\x08"}]),
                           params_nettoyage)
    ligne = df.iloc[0]
    assert ligne["texte"] == "- Jet de charge (voir 11.02)"
    assert ligne["texte_brut"] == "▪Jet de charge (voir 11.02)\x08"
    assert ligne["codes_cites"] == ["11.02"]


def test_annotation_idempotente(fabriquer_elements, params_nettoyage):
    df1 = annoter_nettoyage(fabriquer_elements([{"texte": "▪Jet de charge"}]), params_nettoyage)
    df2 = annoter_nettoyage(df1, params_nettoyage)
    assert df2["texte"].tolist() == df1["texte"].tolist()
    assert df2["texte_brut"].tolist() == df1["texte_brut"].tolist()


def test_repetition_sur_la_meme_page_conservee(fabriquer_elements, params_nettoyage):
    """Deux réponses « R : Non. » dans un même encadré Q/R : aucune n'est un doublon."""
    df = annoter_nettoyage(fabriquer_elements([
        {"texte": "R : Non.", "page": 37},
        {"texte": "R : Non.", "page": 37},
    ]), params_nettoyage)
    assert df["motif_exclusion"].isna().all()


def test_doublon_sur_une_autre_page_exclu(fabriquer_elements, params_nettoyage):
    texte = "EFFET : Résolvez une charge avec votre unité (11.02)."
    df = annoter_nettoyage(fabriquer_elements([
        {"code": "15.11", "texte": texte, "page": 55},
        {"code": "15.11", "texte": texte, "page": 57},
    ]), params_nettoyage)
    assert df["motif_exclusion"].tolist() == [None, "doublon"]


def test_lore_des_stratagemes(fabriquer_elements, params_nettoyage):
    strat = {"code": "15.03", "sous_partie": "STRATAGÈME DE BASE"}
    df = annoter_nettoyage(fabriquer_elements([
        {**strat, "texte": "Les légendes du 41e Millénaire abondent de duels mortels."},
        {**strat, "texte": "QUAND : À la phase de Combat."},
        {**strat, "texte": "CIBLE : Cette unité de PERSONNAGE."},
        {**strat, "texte": "Jusqu'à la fin de la phase, cette figurine a +1."},  # après QUAND
    ]), params_nettoyage)
    assert df["motif_exclusion"].tolist() == ["lore_stratageme", None, None, None]


def test_a_relire_rapport_et_filtrage(fabriquer_elements, params_nettoyage):
    df = annoter_nettoyage(fabriquer_elements([
        {"texte": "Pivot"},
        {"texte": "1PC"},
        {"texte": "Une règle suffisamment longue pour ne pas être signalée."},
    ]), params_nettoyage)
    assert df["a_relire"].tolist() == [True, False, False]
    assert rapport_nettoyage(df).to_dict() == {"conserve": 2, "cout_isole": 1}
    assert contenu_conserve(df)["texte"].tolist() == [
        "Pivot", "Une règle suffisamment longue pour ne pas être signalée.",
    ]
