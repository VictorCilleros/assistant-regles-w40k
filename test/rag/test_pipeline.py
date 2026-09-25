"""Tests du pipeline d'indexation.

- ``controler`` : unitaires, sans base ;
- ``indexer`` : intégration (marqueur ``integration``), avec l'encodeur factice
  de conftest.py et la base éphémère testcontainers.
"""

import logging
import uuid

import numpy as np
import pytest

from assistant_regles.rag.pipeline import controler, indexer
from assistant_regles.rag.store import TABLE, Statistiques


# --------------------------------------------------------------------------- #
# controler (unitaires)
# --------------------------------------------------------------------------- #
def _stats(**surcharges):
    valeurs = {"nb_lignes": 3, "dimensions": [1024], "norme_min": 0.9999,
               "norme_max": 1.0001, "auto_recuperation": 3}
    valeurs.update(surcharges)
    return Statistiques(**valeurs)


def test_controler_ok():
    assert controler(_stats(), nb_attendu=3, dimension=1024) == []


@pytest.mark.parametrize(
    ("surcharges", "motif"),
    [
        ({"nb_lignes": 2}, "ligne"),
        ({"dimensions": [768]}, "dimensions"),
        ({"norme_max": 1.01}, "normes"),
        ({"nb_lignes": 0, "dimensions": [], "norme_min": None, "norme_max": None}, "normes"),
    ],
)
def test_controler_erreurs_bloquantes(surcharges, motif):
    with pytest.raises(ValueError, match=motif):
        controler(_stats(**surcharges), nb_attendu=3, dimension=1024)


def test_controler_auto_recuperation_incomplete_avertit():
    (avertissement,) = controler(_stats(auto_recuperation=2), nb_attendu=3, dimension=1024)
    assert "1 chunk(s)" in avertissement


# --------------------------------------------------------------------------- #
# indexer (intégration)
# --------------------------------------------------------------------------- #
@pytest.fixture
def fabrique_chunks(fabrique_chunk):
    """Chunks aux ids déterministes et aux textes distincts."""

    def fabriquer(n, source="source_test"):
        return [
            fabrique_chunk(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}|{i}")),
                texte=f"CHAPITRE TEST > 01 Section > 01.{i:02d} TITRE\n\nRègle synthétique n° {i}.",
                code=f"01.{i:02d}", source=source, ordres=[i],
            )
            for i in range(n)
        ]

    return fabriquer


@pytest.mark.integration
def test_indexation_complete(conn, encodeur_factice, fabrique_chunks):
    rapport = indexer(fabrique_chunks(5), encodeur_factice, conn)

    perimetre = ("source_test", "11e")
    assert rapport.nb_chunks == 5
    assert rapport.orphelins == {perimetre: 0}
    assert rapport.statistiques[perimetre].nb_lignes == 5
    assert rapport.statistiques[perimetre].auto_recuperation == 5
    assert rapport.avertissements == []
    (modele,) = {r[0] for r in conn.execute(f"SELECT modele_embedding FROM {TABLE}")}
    assert modele == encodeur_factice.identifiant


@pytest.mark.integration
def test_reindexation_apres_redecoupage(conn, encodeur_factice, fabrique_chunks):
    indexer(fabrique_chunks(5), encodeur_factice, conn)
    rapport = indexer(fabrique_chunks(3), encodeur_factice, conn)  # 2 chunks disparus
    assert rapport.orphelins == {("source_test", "11e"): 2}
    assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] == 3


@pytest.mark.integration
def test_aucun_chunk_refuse(conn, encodeur_factice):
    with pytest.raises(ValueError, match="Aucun chunk"):
        indexer([], encodeur_factice, conn)


@pytest.mark.integration
def test_dimension_incoherente_refusee_avant_encodage(conn, encodeur_factice, fabrique_chunks):
    encodeur_factice.dimension = 768
    with pytest.raises(ValueError, match="dimension"):
        indexer(fabrique_chunks(2), encodeur_factice, conn)
    assert encodeur_factice.appels == 0


@pytest.mark.integration
def test_sortie_encodeur_inattendue_refusee(conn, encodeur_factice, fabrique_chunks):
    encodeur_factice.encoder = lambda textes: np.zeros((1, 1024), dtype=np.float32)
    with pytest.raises(ValueError, match="forme"):
        indexer(fabrique_chunks(2), encodeur_factice, conn)
    assert conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] == 0


@pytest.mark.integration
def test_textes_identiques_avertissent(conn, encodeur_factice, fabrique_chunk, caplog):
    chunks = [
        fabrique_chunk(id=str(uuid.uuid4()), texte="Même texte.", ordres=[i]) for i in range(2)
    ]
    with caplog.at_level(logging.WARNING):
        rapport = indexer(chunks, encodeur_factice, conn)
    assert len(rapport.avertissements) == 1
    assert "plus proche voisin" in caplog.text
