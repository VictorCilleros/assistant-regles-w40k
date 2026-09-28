"""Test de l'agent sur la vraie API Anthropic (marqueur ``api``).

Exclu par défaut ; lancer avec ``uv run pytest -m api``. Vérifie le contrat réel :
outils acceptés, appel de retenir_passages, étiquettes valides. Source fictive :
aucune donnée du livre, aucune base.
"""

import uuid

import pytest

from assistant_regles.rag.agent import AgentRecherche, charger_prompt_agent
from assistant_regles.rag.config import charger_config
from assistant_regles.rag.recherche import Resultat

pytestmark = pytest.mark.api


def _resultat(code, texte):
    return Resultat(rang=1, score=0.7, id=uuid.uuid5(uuid.NAMESPACE_URL, code), code=code,
                    chapitre="RÈGLES FICTIVES", section_num="90", section_titre="Test",
                    sous_section=texte.split(".")[0], page_debut=1, page_fin=1, type_contenu="regle",
                    source="test", edition="test", codes_cites=[], texte=texte)


class SourceFictive:
    REGLES = {
        "90.01": "SAUT DE GRENOUILLE. Une unité GRENOUILLE peut sauter jusqu'à 7 pouces au lieu de se déplacer.",
        "90.02": "RESTRICTIONS DU SAUT. Une unité qui a effectué un SAUT DE GRENOUILLE ne peut pas charger ce tour.",
    }

    def rechercher(self, question, k):
        return [_resultat(c, t) for c, t in self.REGLES.items()][:k]

    def lire_regle(self, code):
        return [_resultat(code, self.REGLES[code])] if code in self.REGLES else []


def test_agent_selectionne_les_passages():
    from dotenv import load_dotenv
    import anthropic

    from assistant_regles.ingest.config import trouver_racine

    load_dotenv(trouver_racine() / ".env")
    params = charger_config().agent
    agent = AgentRecherche(anthropic.Anthropic(), SourceFictive(), params, charger_prompt_agent(params))
    selection = agent.chercher("Ma grenouille a sauté, elle peut encore charger ?")
    assert selection.fin == "retenue"
    assert "90.02" in [r.code for r in selection.passages]
