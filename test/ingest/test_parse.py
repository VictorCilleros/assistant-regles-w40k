"""Tests de l'attribution de la structure (titres, pages, parcours du document).

Chaque cas reproduit un piège rencontré sur le vrai livre.
"""

from __future__ import annotations

import pandas as pd
import pytest
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)

from assistant_regles.ingest.parse import (
    ReferentielTitres,
    analyser_titre,
    diagnostiquer_structure,
    extraire_elements,
    localiser,
)


# --------------------------------------------------------------------------- #
# analyser_titre
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("texte", "section", "titre_attendu", "code_attendu"),
    [
        ("ARMÉES 01.01", "01", "ARMÉES", "01.01"),
        ("1. NOM DE LA FICHE TECHNIQUE 02.01", "02", "NOM DE LA FICHE TECHNIQUE", "02.01"),
        ("JOUEUR ACTIF  ET JOUEUR 01.03", "01", "JOUEUR ACTIF ET JOUEUR", "01.03"),
        # coût de stratagème collé au code
        ("RELANCE DE COMMANDEMENT 15.02 1PC", "15", "RELANCE DE COMMANDEMENT", "15.02"),
        ("[ANTI] 24.03 \x08", "24", "[ANTI]", "24.03"),  # caractère de contrôle du PDF
    ],
)
def test_sous_section_codee_reconnue(texte, section, titre_attendu, code_attendu):
    a = analyser_titre(texte, section)
    assert a == {"type": "sous_section", "titre": titre_attendu, "code": code_attendu}


def test_code_d_une_autre_section_est_un_renvoi():
    """« MENEUR 24.22 / APPUI 24.34 » mis en forme comme un titre en page de la section 19."""
    assert analyser_titre("MENEUR 24.22 / APPUI 24.34", "19")["type"] == "renvoi_suspect"


def test_sans_section_connue_le_garde_fou_est_inactif():
    assert analyser_titre("ARMÉES 01.01", None)["type"] == "sous_section"


def test_pseudo_titre_en_deux_points():
    assert analyser_titre("Tant qu'une unité est ébranlée :", "01")["type"] == "pseudo_titre"


def test_titre_sans_code_est_une_sous_partie():
    a = analyser_titre("FINIR UN MOUVEMENT", "03")
    assert a == {"type": "sous_partie", "titre": "FINIR UN MOUVEMENT", "code": None}


def test_titres_de_chapitre_et_de_section_reconnus(structure_mini):
    ref = ReferentielTitres.depuis_structure(structure_mini)
    assert analyser_titre("RÈGLES ÉLÉMENTAIRES", "01", ref)["type"] == "titre_chapitre"
    assert analyser_titre("02 FICHES TECHNIQUES", "02", ref)["type"] == "titre_section"


# --------------------------------------------------------------------------- #
# localiser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("page", "chapitre", "section_num", "section_titre"),
    [
        (None, None, None, None),
        (2, "LIMINAIRE", None, None),
        (6, "RÈGLES ÉLÉMENTAIRES", None, "(ouverture de chapitre)"),
        (9, "RÈGLES ÉLÉMENTAIRES", "01", "Concepts de base"),
        (11, "RÈGLES ÉLÉMENTAIRES", "02", "Fiches techniques"),
        (87, "RÉFÉRENCES", None, "Appendices des règles"),
        (95, "HORS SOMMAIRE", None, None),
    ],
)
def test_localiser(structure_mini, page, chapitre, section_num, section_titre):
    assert localiser(page, structure_mini) == {
        "chapitre": chapitre, "section_num": section_num, "section_titre": section_titre,
    }


# --------------------------------------------------------------------------- #
# extraire_elements (document Docling synthétique)
# --------------------------------------------------------------------------- #
def _prov(page: int) -> ProvenanceItem:
    return ProvenanceItem(page_no=page, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, 0))


@pytest.fixture
def doc_synthetique() -> DoclingDocument:
    """Deux pages : section 01 (p. 8) puis section 02 (p. 10), avec un encadré-image."""
    doc = DoclingDocument(name="synthetique")
    for page in (8, 10):
        doc.add_page(page_no=page, size=Size(width=100, height=100))

    doc.add_heading("ARMÉES 01.01", prov=_prov(8))
    doc.add_text(DocItemLabel.TEXT, "Une armée est composée d'unités.", prov=_prov(8))
    doc.add_heading("COMPOSITION", prov=_prov(8))                         # sous-partie
    doc.add_text(DocItemLabel.TEXT, "Chaque unité a une composition.", prov=_prov(8))
    doc.add_heading("Concepts de base", prov=_prov(8))                    # titre de section
    doc.add_heading("Tant qu'une unité est ébranlée :", prov=_prov(8))   # pseudo-titre

    image = doc.add_picture(prov=_prov(8))                                # encadré « image »
    doc.add_heading("UNITÉS ET FIGURINES 01.02", prov=_prov(8), parent=image)
    doc.add_text(DocItemLabel.TEXT, "Texte dans un encadré.", prov=_prov(8), parent=image)

    doc.add_heading("MENEUR 24.22 / APPUI 24.34", prov=_prov(8))         # renvoi suspect
    doc.add_text(DocItemLabel.TEXT, "Après le renvoi.", prov=_prov(8))

    doc.add_text(DocItemLabel.TEXT, "Introduction des fiches.", prov=_prov(10))  # section 02
    return doc


def test_extraire_elements(doc_synthetique, structure_mini):
    df, reperes = extraire_elements(doc_synthetique, structure_mini)
    par_texte = df.set_index("texte")

    armee = par_texte.loc["Une armée est composée d'unités."]
    assert (armee["code"], armee["sous_section"]) == ("01.01", "ARMÉES")
    assert pd.isna(armee["sous_partie"])
    assert par_texte.loc["Chaque unité a une composition.", "sous_partie"] == "COMPOSITION"

    # Le pseudo-titre reste du contenu, les titres de chapitre / section non.
    assert par_texte.loc["Tant qu'une unité est ébranlée :", "type_contenu"] == "pseudo_titre"
    assert "Concepts de base" not in par_texte.index

    # Le texte de l'encadré-image est récupéré, marqué, et son titre codé est pris en compte.
    encadre = par_texte.loc["Texte dans un encadré."]
    assert bool(encadre["dans_image"]) and encadre["code"] == "01.02"

    # Le renvoi ne change pas le code courant ; il devient une simple sous-partie.
    apres = par_texte.loc["Après le renvoi."]
    assert (apres["code"], apres["sous_partie"]) == ("01.02", "MENEUR 24.22 / APPUI 24.34")

    # Changement de section (déduit de la page) -> état réinitialisé.
    intro = par_texte.loc["Introduction des fiches."]
    assert intro["section_num"] == "02" and pd.isna(intro["code"]) and pd.isna(intro["sous_partie"])

    assert "renvoi_suspect" in set(reperes["type"])


# --------------------------------------------------------------------------- #
# diagnostiquer_structure
# --------------------------------------------------------------------------- #
def test_diagnostic_structure():
    reperes = pd.DataFrame([
        {"type": "sous_section", "code": "01.01", "section_num_page": "01"},
        {"type": "sous_section", "code": "01.03", "section_num_page": "01"},  # 01.02 absent
        {"type": "sous_section", "code": "15.11", "section_num_page": "15"},
        {"type": "sous_section", "code": "15.11", "section_num_page": "15"},  # doublon
        {"type": "sous_section", "code": "24.34", "section_num_page": "19"},  # incohérent
        {"type": "sous_partie", "code": None, "section_num_page": "01"},
    ])
    diag = diagnostiquer_structure(reperes)
    assert diag["incoherents"] == ["24.34"]
    assert diag["manquants"]["01"] == [2]
    assert diag["doublons"] == {"15.11": 2}
