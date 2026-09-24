"""Tests du découpage en chunks.

Le compteur de tokens est factice (un mot = un token) et le budget réduit
(cible 25, max 30) : on vérifie la logique, pas le tokenizer.

Repère de calcul : le préfixe « CHAP > 01 Sec > 01.01 TITRE » fait 7 mots,
un titre de sous-partie « ### SPx » en fait 2.
"""

from __future__ import annotations

from collections import Counter

import pytest

from assistant_regles.ingest.chunk import (
    blocs_sous_parties,
    construire_chunks,
    construire_prefixe,
    ecrire_jsonl,
    lire_jsonl,
    rendre_unite,
)

UNITE = {"chapitre": "CHAP", "section_num": "01", "section_titre": "Sec",
         "code": "01.01", "sous_section": "TITRE"}


def _mots(n: int, prefixe: str = "mot") -> str:
    return " ".join(f"{prefixe}{i}" for i in range(n))


@pytest.fixture
def chunker(params_chunking, document, compter_mots):
    """Découpe un DataFrame d'éléments avec le budget et le compteur de test."""
    def _chunker(df):
        return construire_chunks(df, params_chunking, document, compter_mots)
    return _chunker


# --------------------------------------------------------------------------- #
# Rendu
# --------------------------------------------------------------------------- #
def test_prefixe_complet_et_partiel():
    prefixe = construire_prefixe("CHAP", "01", "Sec", "01.01", "TITRE")
    assert prefixe == "CHAP > 01 Sec > 01.01 TITRE"
    assert construire_prefixe("RÉFÉRENCES", None, "Appendices des règles", None, None) == \
        "RÉFÉRENCES > Appendices des règles"


def test_rendu_reinsere_les_titres_de_sous_parties(fabriquer_elements):
    df = fabriquer_elements([
        {"sous_partie": "FINIR UN MOUVEMENT", "texte": "Paragraphe 1."},
        {"sous_partie": "FINIR UN MOUVEMENT", "texte": "Paragraphe 2."},
        {"sous_partie": None, "type_contenu": "table", "texte": "| a | b |"},
    ])
    texte = rendre_unite(df, "PREFIXE")
    assert texte.startswith("PREFIXE")
    assert texte.count("### FINIR UN MOUVEMENT") == 1
    assert "\n\n| a | b |" in texte  # tableau séparé par une ligne vide


def test_blocs_de_sous_parties_contigues(fabriquer_elements):
    """Deux sous-parties homonymes non contiguës restent deux blocs distincts."""
    df = fabriquer_elements([{"sous_partie": sp} for sp in ["A", "A", "B", "A"]])
    assert [len(b) for b in blocs_sous_parties(df)] == [2, 1, 1]


# --------------------------------------------------------------------------- #
# Découpage
# --------------------------------------------------------------------------- #
def test_petite_unite_un_seul_chunk(fabriquer_elements, chunker):
    chunks = chunker(fabriquer_elements([{**UNITE, "texte": _mots(5)}]))
    assert len(chunks) == 1
    assert (chunks[0].partie, chunks[0].nb_parties) == (1, 1)
    assert chunks[0].texte.startswith("CHAP > 01 Sec > 01.01 TITRE")


def test_grosse_unite_decoupee_par_sous_parties(fabriquer_elements, chunker):
    """4 sous-parties de 6 mots (titre + 4 mots) : 7 + 4×6 = 31 > 30 -> découpage.

    Regroupement jusqu'à la cible 25 : [SP1, SP2, SP3] = 25, puis [SP4] = 13.
    """
    df = fabriquer_elements(
        [{**UNITE, "sous_partie": f"SP{i}", "texte": _mots(4)} for i in range(1, 5)]
    )
    chunks = chunker(df)
    assert [c.sous_parties for c in chunks] == [["SP1", "SP2", "SP3"], ["SP4"]]
    assert [c.nb_tokens for c in chunks] == [25, 13]
    assert all(c.nb_parties == 2 for c in chunks)

    # Pas de chevauchement entre sous-parties : chaque élément apparaît une seule fois.
    tous = [o for c in chunks for o in c.ordres]
    assert sorted(tous) == df["ordre"].tolist()
    assert all(c.texte.startswith("CHAP > 01 Sec > 01.01 TITRE") for c in chunks)


def test_sous_partie_trop_grosse_decoupee_avec_chevauchement(fabriquer_elements, chunker):
    """Une sous-partie de 6 éléments de 5 mots (7 + 2 + 30 = 39 > 30).

    Attendu : [e0, e1, e2], [e2, e3, e4], [e4, e5] — e2 et e4 répétés.
    """
    df = fabriquer_elements(
        [{**UNITE, "sous_partie": "SPX", "texte": _mots(5, f"e{i}_")} for i in range(6)]
    )
    chunks = chunker(df)
    assert [c.ordres for c in chunks] == [[0, 1, 2], [2, 3, 4], [4, 5]]
    repetes = {o for o, n in Counter(o for c in chunks for o in c.ordres).items() if n > 1}
    assert repetes == {2, 4}
    assert all(c.texte.count("### SPX") == 1 for c in chunks)  # titre répété dans chaque morceau


def test_element_seul_hors_budget_garde_entier(fabriquer_elements, chunker):
    chunks = chunker(fabriquer_elements([{**UNITE, "type_contenu": "table", "texte": _mots(40)}]))
    assert len(chunks) == 1
    assert chunks[0].hors_budget and chunks[0].type_contenu == "table"


def test_unites_separees_par_code(fabriquer_elements, chunker):
    df = fabriquer_elements([
        {**UNITE, "texte": "Règle A."},
        {**UNITE, "code": "01.02", "sous_section": "AUTRE", "texte": "Règle B."},
    ])
    assert [c.code for c in chunker(df)] == ["01.01", "01.02"]


# --------------------------------------------------------------------------- #
# Métadonnées, identifiants, E/S
# --------------------------------------------------------------------------- #
def test_metadonnees(fabriquer_elements, chunker):
    df = fabriquer_elements([
        {**UNITE, "page": 12, "texte": "Voir 03.03.", "codes_cites": ["03.03", "01.01"]},
        {**UNITE, "page": 13, "texte": "Suite."},
    ])
    chunk = chunker(df)[0]
    assert (chunk.page_debut, chunk.page_fin) == (12, 13)
    assert chunk.codes_cites == ["03.03"]  # le code du chunk lui-même est retiré
    assert (chunk.source, chunk.edition, chunk.type_contenu) == ("livre_test", "11e", "regle")


def test_identifiants_stables_et_uniques(fabriquer_elements, chunker):
    df = fabriquer_elements(
        [{**UNITE, "sous_partie": f"SP{i}", "texte": _mots(4)} for i in range(1, 5)]
    )
    ids_1 = [c.id for c in chunker(df)]
    ids_2 = [c.id for c in chunker(df)]
    assert ids_1 == ids_2
    assert len(set(ids_1)) == len(ids_1)


def test_aller_retour_jsonl(fabriquer_elements, chunker, tmp_path):
    chunks = chunker(fabriquer_elements([{**UNITE, "texte": 'Portée 24" et ½" — « Détruit »'}]))
    chemin = ecrire_jsonl(chunks, tmp_path / "sortie" / "chunks.jsonl")
    assert lire_jsonl(chemin) == chunks
    assert "½" in chemin.read_text(encoding="utf-8")  # UTF-8 lisible, pas d'échappement \u
