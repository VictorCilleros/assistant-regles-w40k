"""Tests de la recherche top-k.

- validation et mise en forme : unitaires, sans base ;
- MoteurRecherche : intégration (marqueur ``integration``), avec l'encodeur
  factice de conftest.py et la base éphémère testcontainers.
"""

import uuid
from dataclasses import fields

import pytest

from assistant_regles.rag.recherche import (
    COLONNES_RESULTAT,
    MoteurRecherche,
    Resultat,
    formater_resultat,
    valider_requete,
)
from assistant_regles.rag.store import TABLE, synchroniser, vers_ligne


# --------------------------------------------------------------------------- #
# Unitaires
# --------------------------------------------------------------------------- #
def _resultat(**surcharges):
    valeurs = {
        "rang": 1, "score": 0.6123, "id": uuid.uuid4(), "code": "24.27",
        "chapitre": "CHAPITRE", "section_num": "24", "section_titre": "Section",
        "sous_section": "SOUS-SECTION", "page_debut": 57, "page_fin": 58,
        "type_contenu": "regle", "source": "livre", "edition": "11e",
        "codes_cites": [], "texte": "Ligne un.\n\nLigne   deux.",
    }
    valeurs.update(surcharges)
    return Resultat(**valeurs)


def test_colonnes_sql_et_champs_du_resultat_coherents():
    """Chaque colonne lue en SQL a son champ dans Resultat, en plus du rang et du score."""
    assert {f.name for f in fields(Resultat)} == set(COLONNES_RESULTAT) | {"rang", "score"}


@pytest.mark.parametrize(("question", "k"), [("", 5), ("   ", 5), ("question", 0), ("question", -1)])
def test_valider_requete_refuse(question, k):
    with pytest.raises(ValueError):
        valider_requete(question, k)


def test_formater_apercu():
    texte = formater_resultat(_resultat())
    entete, apercu = texte.splitlines()[:2]
    assert entete == "#1  0.612  24.27  |  24 Section > SOUS-SECTION  |  p. 57-58  |  regle"
    assert apercu.strip() == "Ligne un. Ligne deux."  # blancs et retours à la ligne réduits


def test_formater_apercu_tronque():
    texte = formater_resultat(_resultat(texte="mot " * 100), largeur=20)
    assert texte.splitlines()[1].strip().endswith("…")


def test_formater_texte_complet_et_sans_code():
    texte = formater_resultat(_resultat(code=None, page_fin=57), texte_complet=True)
    assert "(sans code)" in texte and "p. 57  |" in texte
    assert "Ligne un.\n\nLigne   deux." in texte  # texte intact


# --------------------------------------------------------------------------- #
# Intégration
# --------------------------------------------------------------------------- #
@pytest.fixture
def base_remplie(conn, encodeur_factice, fabrique_chunk):
    """Base de 6 chunks indexés avec l'encodeur factice ; renvoie les chunks."""
    chunks = [
        fabrique_chunk(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"recherche|{i}")),
            texte=f"CHAPITRE TEST > 01 Section > 01.{i:02d} TITRE\n\nRègle synthétique n° {i}.",
            code=f"01.{i:02d}", page_debut=10 + i, page_fin=10 + i, ordres=[i],
        )
        for i in range(6)
    ]
    vecteurs = encodeur_factice.encoder([c.texte for c in chunks])
    synchroniser(conn, [vers_ligne(c, v, encodeur_factice.identifiant) for c, v in zip(chunks, vecteurs)])
    return chunks


@pytest.mark.integration
def test_table_vide_refusee(conn, encodeur_factice):
    with pytest.raises(RuntimeError, match="vide"):
        MoteurRecherche(encodeur_factice, conn)


@pytest.mark.integration
def test_table_absente_refusee(conn, encodeur_factice):
    conn.execute(f"DROP TABLE {TABLE}")  # la fixture conn la recrée au test suivant
    with pytest.raises(RuntimeError, match="absente"):
        MoteurRecherche(encodeur_factice, conn)


@pytest.mark.integration
def test_autre_modele_refuse(conn, encodeur_factice, base_remplie):
    encodeur_factice.identifiant = "autre-modele@1"
    with pytest.raises(ValueError, match="non comparables"):
        MoteurRecherche(encodeur_factice, conn)


@pytest.mark.integration
def test_melange_de_modeles_refuse(conn, encodeur_factice, base_remplie):
    conn.execute(f"UPDATE {TABLE} SET modele_embedding = 'ancien@0' WHERE code = '01.00'")
    with pytest.raises(ValueError, match="non comparables"):
        MoteurRecherche(encodeur_factice, conn)


@pytest.mark.integration
def test_texte_exact_retrouve_en_premier(conn, encodeur_factice, base_remplie):
    """Avec l'encodeur factice, un texte identique donne un vecteur identique : score 1."""
    cible = base_remplie[3]
    (premier, *_) = MoteurRecherche(encodeur_factice, conn).rechercher(cible.texte, k=3)
    assert str(premier.id) == str(cible.id)
    assert premier.score == pytest.approx(1.0, abs=1e-5)
    assert (premier.code, premier.page_debut, premier.texte) == ("01.03", 13, cible.texte)


@pytest.mark.integration
def test_k_resultats_ordonnes(conn, encodeur_factice, base_remplie):
    resultats = MoteurRecherche(encodeur_factice, conn).rechercher("Une question quelconque", k=4)
    assert [r.rang for r in resultats] == [1, 2, 3, 4]
    scores = [r.score for r in resultats]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.integration
def test_k_superieur_au_corpus(conn, encodeur_factice, base_remplie):
    resultats = MoteurRecherche(encodeur_factice, conn).rechercher("Une question", k=50)
    assert len(resultats) == len(base_remplie)


@pytest.mark.integration
def test_question_vide_refusee_sans_encodage(conn, encodeur_factice, base_remplie):
    moteur = MoteurRecherche(encodeur_factice, conn)
    with pytest.raises(ValueError, match="vide"):
        moteur.rechercher("  ", k=5)
    assert encodeur_factice.appels == 1  # seul l'encodage de base_remplie
