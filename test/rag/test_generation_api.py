"""Test sur la vraie API Anthropic (marqueur ``api``).

Exclu par défaut ; lancer avec ``uv run pytest -m api`` (ANTHROPIC_API_KEY
requise, coût de l'ordre du centime). Vérifie le contrat réel de l'API :
blocs search_result acceptés, citations renvoyées, paramètres acceptés.
Contenu entièrement fictif : aucune donnée du livre.
"""

import uuid

import pytest

from assistant_regles.rag.config import charger_config
from assistant_regles.rag.generation import Generateur, charger_prompt
from assistant_regles.rag.recherche import Resultat

pytestmark = pytest.mark.api

REGLE_FICTIVE = (
    "RÈGLES FICTIVES > 90 Test > 90.01 SAUT DE GRENOUILLE\n\n"
    "Une unité GRENOUILLE peut effectuer un SAUT DE GRENOUILLE au lieu de se déplacer normalement.\n\n"
    "Un SAUT DE GRENOUILLE permet de franchir jusqu'à 7 pouces, en ignorant les figurines ennemies.\n\n"
    "Une unité qui a effectué un SAUT DE GRENOUILLE ne peut pas charger pendant ce tour."
)


@pytest.fixture(scope="module")
def generateur():
    import anthropic
    from dotenv import load_dotenv

    from assistant_regles.ingest.config import trouver_racine

    load_dotenv(trouver_racine() / ".env")
    config = charger_config()
    prompt = charger_prompt(config.generation.prompt_systeme, config.generation.phrase_abstention)
    return Generateur(anthropic.Anthropic(), config.generation, prompt)


@pytest.fixture
def resultats():
    return [Resultat(
        rang=1, score=0.7, id=uuid.uuid4(), code="90.01", chapitre="RÈGLES FICTIVES",
        section_num="90", section_titre="Test", sous_section="SAUT DE GRENOUILLE",
        page_debut=1, page_fin=1, type_contenu="regle", source="test", edition="test",
        codes_cites=[], texte=REGLE_FICTIVE,
    )]


def test_reponse_citee(generateur, resultats):
    reponse = generateur.generer("Une unité GRENOUILLE peut-elle charger après un SAUT DE GRENOUILLE ?", resultats)
    assert reponse.stop_reason == "end_turn"
    assert not reponse.abstention
    assert reponse.citations, "aucune citation renvoyée"
    assert all(c.code == "90.01" for c in reponse.citations)


def test_abstention_hors_corpus(generateur, resultats):
    reponse = generateur.generer("Quelle est la recette de la pâte à crêpes ?", resultats)
    assert reponse.abstention
