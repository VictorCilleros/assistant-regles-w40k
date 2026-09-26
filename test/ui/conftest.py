"""Fixtures de l'interface : réponses factices, sans base, modèle ni API."""

import uuid
from types import SimpleNamespace as NS

import pytest

from assistant_regles.rag.config import ParamsGeneration
from assistant_regles.rag.generation import Generateur, PromptSysteme
from assistant_regles.rag.recherche import Resultat

PHRASE = ParamsGeneration().phrase_abstention


def fabriquer_resultat(i: int = 0) -> Resultat:
    return Resultat(
        rang=i + 1, score=0.6, id=uuid.uuid4(), code=f"24.2{i}", chapitre="CHAPITRE",
        section_num="24", section_titre="Armes", sous_section="PISTOLET", page_debut=57,
        page_fin=57, type_contenu="regle", source="livre_regles_principal", edition="11e",
        codes_cites=[], texte=f"PREFIXE > 24.2{i}\n\nParagraphe un.\n\nParagraphe deux.",
    )


def _citation(index, debut=1, fin=2):
    return NS(type="search_result_location", search_result_index=index, start_block_index=debut,
              end_block_index=fin, cited_text="Paragraphe un.", title="24.20 PISTOLET — p. 57", source="chunk:x")


def faux_message():
    return NS(
        content=[NS(type="text", text="Oui.", citations=None),
                 NS(type="text", text=" Un PISTOLET peut tirer ($1$ fois)", citations=[_citation(0)])],
        stop_reason="end_turn", usage=NS(input_tokens=2400, output_tokens=120),
    )


class FauxFlux:
    def __init__(self, message):
        self._message = message
        self.text_stream = iter(["Oui.", " Un PISTOLET", " peut tirer ($1$ fois)"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FauxClient:
    def __init__(self):
        self.messages = NS(stream=lambda **kw: FauxFlux(faux_message()), create=lambda **kw: faux_message())


class FauxMoteur:
    def rechercher(self, question, k):
        return [fabriquer_resultat(i) for i in range(k)]


@pytest.fixture
def reponse_factice():
    prompt = PromptSysteme("systeme_test.md", f"… {PHRASE}", "b" * 64)
    return Generateur(FauxClient(), ParamsGeneration(), prompt).generer("Q ?", [fabriquer_resultat(0)])


@pytest.fixture
def ressources_factices(monkeypatch):
    """Remplace les ressources lourdes de l'interface par des faux."""
    from assistant_regles.ui import ressources

    prompt = PromptSysteme("systeme_test.md", f"… {PHRASE}", "b" * 64)
    monkeypatch.setattr(ressources, "moteur_recherche", lambda: FauxMoteur())
    monkeypatch.setattr(ressources, "generateur",
                        lambda config_rag: Generateur(FauxClient(), config_rag.generation, prompt))
    monkeypatch.setattr(ressources, "compter_chunks", lambda: {("livre_regles_principal", "11e"): 207})
