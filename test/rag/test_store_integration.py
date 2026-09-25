"""Tests d'intégration de store.py sur un PostgreSQL + pgvector éphémère.

Marqueur ``integration`` : lancer avec ``uv run pytest -m integration``
(Docker doit tourner). Le conteneur est créé une fois par session ; chaque test
repart d'une table vide (fixture ``conn``).
"""

import uuid

import numpy as np
import psycopg
import pytest

from assistant_regles.rag.store import (
    TABLE,
    appliquer_schema,
    statistiques,
    synchroniser,
    verifier_dimension,
    vers_ligne,
)

pytestmark = pytest.mark.integration

MODELE = "modele-test@0000"


@pytest.fixture
def fabrique_lignes(fabrique_chunk, fabrique_vecteur):
    """Lignes aux ids déterministes : relancer donne les mêmes ids, comme l'ingestion."""

    def fabriquer(n, source="source_test", edition="11e", depart=0):
        lignes = []
        for i in range(depart, depart + n):
            id_ = uuid.uuid5(uuid.NAMESPACE_URL, f"{source}|{edition}|{i}")
            chunk = fabrique_chunk(id=str(id_), source=source, edition=edition, ordres=[i])
            lignes.append(vers_ligne(chunk, fabrique_vecteur(i), MODELE))
        return lignes

    return fabriquer


def _compter(conn, source="source_test"):
    return conn.execute(f"SELECT count(*) FROM {TABLE} WHERE source = %s", (source,)).fetchone()[0]


# --------------------------------------------------------------------------- #
# Schéma
# --------------------------------------------------------------------------- #
def test_schema_idempotent(conn):
    appliquer_schema(conn)  # deuxième application : aucune erreur
    index = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (TABLE,))}
    assert index == {f"{TABLE}_pkey", f"{TABLE}_embedding_hnsw"}


def test_verifier_dimension(conn):
    verifier_dimension(conn, 1024)
    with pytest.raises(ValueError, match="dimension"):
        verifier_dimension(conn, 768)


# --------------------------------------------------------------------------- #
# Synchronisation
# --------------------------------------------------------------------------- #
def test_insertion_et_aller_retour(conn, fabrique_lignes):
    lignes = fabrique_lignes(3)
    assert synchroniser(conn, lignes) == {("source_test", "11e"): 0}
    assert _compter(conn) == 3

    relu = conn.execute(
        f"SELECT embedding, sous_parties, ordres FROM {TABLE} WHERE id = %s", (lignes[0]["id"],)
    ).fetchone()
    assert np.array_equal(relu[0].to_numpy(), lignes[0]["embedding"])
    assert relu[1] == [] and relu[2] == [0]


def test_relance_idempotente(conn, fabrique_lignes):
    synchroniser(conn, fabrique_lignes(3))
    assert synchroniser(conn, fabrique_lignes(3)) == {("source_test", "11e"): 0}
    assert _compter(conn) == 3


def test_mise_a_jour_sur_meme_id(conn, fabrique_lignes):
    lignes = fabrique_lignes(2)
    synchroniser(conn, lignes)

    lignes[0] = {**lignes[0], "texte": "texte corrigé", "empreinte_texte": "e" * 64}
    synchroniser(conn, lignes)

    texte, empreinte_texte = conn.execute(
        f"SELECT texte, empreinte_texte FROM {TABLE} WHERE id = %s", (lignes[0]["id"],)
    ).fetchone()
    assert (texte, empreinte_texte) == ("texte corrigé", "e" * 64)
    assert _compter(conn) == 2


def test_orphelins_supprimes(conn, fabrique_lignes):
    lignes = fabrique_lignes(3)
    synchroniser(conn, lignes)
    assert synchroniser(conn, lignes[:2]) == {("source_test", "11e"): 1}
    assert _compter(conn) == 2


def test_autre_perimetre_intact(conn, fabrique_lignes):
    synchroniser(conn, fabrique_lignes(2, source="livre"))
    synchroniser(conn, fabrique_lignes(2, source="codex"))

    synchroniser(conn, fabrique_lignes(1, source="livre"))  # réindexation du livre seul
    assert _compter(conn, "livre") == 1
    assert _compter(conn, "codex") == 2


def test_lot_vide_ne_touche_rien(conn, fabrique_lignes):
    synchroniser(conn, fabrique_lignes(2))
    assert synchroniser(conn, []) == {}
    assert _compter(conn) == 2


def test_atomicite(conn, fabrique_lignes):
    """Une ligne invalide annule tout le lot : ni upsert partiel, ni suppression."""
    lignes = fabrique_lignes(2)
    synchroniser(conn, lignes)

    nouvelle = fabrique_lignes(1, depart=10)[0]
    invalide = {**fabrique_lignes(1, depart=11)[0], "embedding": np.ones(3, dtype=np.float32)}
    with pytest.raises(psycopg.errors.DataException):
        synchroniser(conn, [nouvelle, invalide])  # aurait aussi supprimé les 2 anciennes

    ids = {r[0] for r in conn.execute(f"SELECT id FROM {TABLE}")}
    assert ids == {ligne["id"] for ligne in lignes}


# --------------------------------------------------------------------------- #
# Contrôles
# --------------------------------------------------------------------------- #
def test_statistiques(conn, fabrique_lignes):
    synchroniser(conn, fabrique_lignes(5))
    stats = statistiques(conn, "source_test", "11e")
    assert stats.nb_lignes == 5
    assert stats.dimensions == [1024]
    assert stats.norme_min == pytest.approx(1.0, abs=1e-5)
    assert stats.norme_max == pytest.approx(1.0, abs=1e-5)
    assert stats.auto_recuperation == 5


def test_statistiques_perimetre_vide(conn):
    stats = statistiques(conn, "absente", "11e")
    assert (stats.nb_lignes, stats.dimensions, stats.norme_min, stats.auto_recuperation) == (0, [], None, 0)
