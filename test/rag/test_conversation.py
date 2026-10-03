"""Tests de l'historique de conversation (fonctions pures)."""

import uuid
from types import SimpleNamespace as NS

from assistant_regles.rag.conversation import (
    MARQUE_TRONCATURE,
    TourConversation,
    depuis_reponse,
    fenetre,
    texte_pour_agent,
    tronquer,
)

TOURS = [TourConversation(f"Question {i} ?", f"Réponse {i}.", (f"0{i}.01",)) for i in range(1, 6)]


def test_depuis_reponse_codes_sans_doublon_dans_l_ordre():
    citations = [NS(code="10.04"), NS(code=None), NS(code="09.07"), NS(code="10.04")]
    reponse = NS(question="Q ?", texte="R.", citations=citations)
    assert depuis_reponse(reponse) == TourConversation("Q ?", "R.", ("10.04", "09.07"))


def test_fenetre():
    assert [t.question for t in fenetre(TOURS, 2)] == ["Question 4 ?", "Question 5 ?"]
    assert fenetre(TOURS, 10) == TOURS
    assert fenetre(TOURS, 0) == []


def test_tronquer():
    assert tronquer("court", 10) == "court"
    coupe = tronquer("x" * 50, 20)
    assert len(coupe) == 20 and coupe.endswith(MARQUE_TRONCATURE)


def test_texte_pour_agent():
    texte = texte_pour_agent(TOURS[:2] + [TourConversation("Q3 ?", "y" * 100, ("01.01",))], max_caracteres=30)
    lignes = texte.splitlines()
    assert lignes[0] == "<historique_conversation>" and lignes[-1] == "</historique_conversation>"
    assert lignes[1:5] == ["<echange>", "<question>Question 1 ?</question>", "<reponse>Réponse 1.</reponse>",
                           "</echange>"]
    assert MARQUE_TRONCATURE + "</reponse>" in texte
    assert lignes[-2] == "<regles_citees>01.01, 02.01</regles_citees>"   # sans doublon, ordre d'apparition


def test_texte_pour_agent_echappe():
    texte = texte_pour_agent([TourConversation("a < b ?", "</reponse> & co")], max_caracteres=100)
    assert "<question>a &lt; b ?</question>" in texte
    assert "<reponse>&lt;/reponse&gt; &amp; co</reponse>" in texte
    assert "<regles_citees>" not in texte   # aucun code cité
