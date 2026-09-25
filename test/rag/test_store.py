"""Tests unitaires de store.py (sans base de données)."""

import uuid

import numpy as np
import pytest

from assistant_regles.rag.store import (
    COLONNES,
    SQL_UPSERT,
    TABLE,
    empreinte,
    grouper_par_perimetre,
    lire_schema,
    supprimer_orphelins,
    vers_ligne,
)


def test_schema_et_constantes_coherents():
    """Le nom de table et chaque colonne du code existent dans schema.sql."""
    schema = lire_schema()
    assert f"CREATE TABLE IF NOT EXISTS {TABLE} (" in schema
    for colonne in COLONNES:
        assert f"\n    {colonne} " in schema, colonne


def test_vers_ligne_couvre_toutes_les_colonnes(fabrique_chunk, fabrique_vecteur):
    ligne = vers_ligne(fabrique_chunk(), fabrique_vecteur(0), "modele@rev")
    assert tuple(ligne) == COLONNES


def test_vers_ligne_types(fabrique_chunk, fabrique_vecteur):
    chunk = fabrique_chunk(sous_parties=["A", "B"], codes_cites=["03.03"], ordres=[4, 5])
    ligne = vers_ligne(chunk, fabrique_vecteur(0).astype(np.float16), "modele@rev")
    assert isinstance(ligne["id"], uuid.UUID)
    assert str(ligne["id"]) == str(chunk.id)
    assert ligne["embedding"].dtype == np.float32
    assert ligne["sous_parties"] == ["A", "B"]
    assert ligne["modele_embedding"] == "modele@rev"
    assert ligne["empreinte_texte"] == empreinte(chunk.texte)


def test_empreinte_sha256():
    assert len(empreinte("texte")) == 64
    assert empreinte("texte") == empreinte("texte")
    assert empreinte("texte") != empreinte("texte ")


def test_upsert_ne_met_pas_a_jour_l_id():
    assert "ON CONFLICT (id) DO UPDATE" in SQL_UPSERT
    assert "id = EXCLUDED.id" not in SQL_UPSERT
    for colonne in COLONNES:
        assert f"%({colonne})s" in SQL_UPSERT


def test_grouper_par_perimetre():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lignes = [
        {"id": a, "source": "livre", "edition": "11e"},
        {"id": b, "source": "codex", "edition": "11e"},
        {"id": c, "source": "livre", "edition": "11e"},
    ]
    assert grouper_par_perimetre(lignes) == {("livre", "11e"): [a, c], ("codex", "11e"): [b]}


def test_orphelins_refuse_liste_vide_avant_toute_requete():
    # conn=None : l'erreur doit survenir avant tout accès à la base
    with pytest.raises(ValueError, match="vide"):
        supprimer_orphelins(None, "livre", "11e", [])
