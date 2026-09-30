"""Tests de l'agent de recherche avec un faux client et une fausse source (ni réseau, ni base)."""

import uuid
from types import SimpleNamespace as NS

import pytest

from assistant_regles.rag.agent import (
    RAPPEL_BUDGET,
    AgentRecherche,
    AppelOutil,
    charger_prompt_agent,
    decrire_appel,
    definir_outils,
)
from assistant_regles.rag.config import ParamsAgent
from assistant_regles.rag.generation import PromptSysteme
from assistant_regles.rag.recherche import Resultat


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #
def _resultat(code, renvois=()):
    return Resultat(
        rang=1, score=0.6, id=uuid.uuid5(uuid.NAMESPACE_URL, code), code=code, chapitre="C",
        section_num=code[:2], section_titre="Section", sous_section=f"TITRE {code}", page_debut=10,
        page_fin=10, type_contenu="regle", source="livre", edition="11e", codes_cites=list(renvois),
        texte=f"PREFIXE > {code}\n\nTexte de la règle {code}.",
    )


class FausseSource:
    """Amorce : 01.01 et 01.02 ; requête « AVANCER » : 03.04 et 01.01 (déjà vu) ; lecture par code."""

    def __init__(self):
        self.recherches, self.lectures = [], []

    def rechercher(self, question, k):
        self.recherches.append((question, k))
        if "AVANCER" in question:
            return [_resultat("03.04", ["05.03"]), _resultat("01.01")]
        return [_resultat("01.01"), _resultat("01.02")][:k]

    def lire_regle(self, code):
        self.lectures.append(code)
        if code == "99.99":
            raise ValueError("code inconnu")
        return [_resultat(code)]


def _outil(nom, entree, identifiant="t"):
    return NS(type="tool_use", id=f"{identifiant}-{nom}", name=nom, input=entree)


def _message(*blocs, stop_reason="tool_use"):
    return NS(content=list(blocs), stop_reason=stop_reason, usage=NS(input_tokens=1000, output_tokens=100))


class FauxClient:
    """Renvoie les messages préparés, un par tour ; garde une copie de ce qui a été envoyé."""

    def __init__(self, *messages):
        self._messages = list(messages)
        self.envois = []
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.envois.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return self._messages.pop(0)


PROMPT = PromptSysteme("agent_test.md", "Prompt de test.", "a" * 64)


def _agent(client, source=None, **params):
    return AgentRecherche(client, source or FausseSource(), ParamsAgent(**params), PROMPT)


# --------------------------------------------------------------------------- #
# Prompt, outils, paramètres
# --------------------------------------------------------------------------- #
def test_prompt_du_repo_sans_marqueur_restant():
    prompt = charger_prompt_agent(ParamsAgent(max_passages=7, max_tours=5))
    assert "{" not in prompt.texte
    assert "au maximum 7" in prompt.texte and "au plus 4 tours" in prompt.texte


def test_outils():
    outils = {o["name"]: o for o in definir_outils(max_passages=6)}
    assert set(outils) == {"rechercher_regles", "lire_regle", "retenir_passages"}
    assert outils["retenir_passages"]["input_schema"]["properties"]["etiquettes"]["maxItems"] == 6
    assert outils["lire_regle"]["input_schema"]["properties"]["code"]["pattern"] == r"^\d{2}\.\d{2}$"


@pytest.mark.parametrize(("effort", "attendu"), [("high", {"effort": "high"}), (None, None)])
def test_parametres_appel(effort, attendu):
    parametres = _agent(None, effort=effort).parametres_appel([])
    assert parametres["model"] == "claude-sonnet-5"
    assert parametres.get("output_config") == attendu
    assert "temperature" not in parametres and "tool_choice" not in parametres


def test_decrire_appel():
    assert decrire_appel(AppelOutil("rechercher_regles", "AVANCER", ("03.04", None))) == \
        "Recherche « AVANCER » → 03.04, (sans code)"
    assert decrire_appel(AppelOutil("lire_regle", "99.99", erreur="code inconnu")).endswith("erreur : code inconnu")


# --------------------------------------------------------------------------- #
# Boucle
# --------------------------------------------------------------------------- #
def test_recherche_complete():
    """Amorce, deux outils (dont un en erreur), puis sélection avec doublon et étiquette inventée."""
    client = FauxClient(
        _message(NS(type="thinking", thinking="courir = AVANCER"),
                 _outil("rechercher_regles", {"requete": "AVANCER puis TIRER"}),
                 _outil("lire_regle", {"code": "05.03"}),
                 _outil("lire_regle", {"code": "99.99"}, "b")),
        _message(_outil("retenir_passages", {"etiquettes": ["P3", "P1", "P3", "P42"], "justification": "ok"})),
    )
    source = FausseSource()
    etapes = []
    selection = _agent(client, source, min_passages=0).chercher("Mes gars ont couru, ils tirent ?",
                                                                au_fil=etapes.append)

    assert selection.fin == "retenue"
    assert [r.code for r in selection.passages] == ["03.04", "01.01"]  # ordre de l'agent, sans doublon ni inventée
    assert selection.vus == 4 and selection.tours == 2
    assert (selection.tokens_entree, selection.tokens_sortie) == (2000, 200)
    assert selection.nb_appels_outils == 3
    assert [e.outil for e in etapes] == ["amorce", "rechercher_regles", "lire_regle", "lire_regle", "retenir_passages"]
    assert etapes[3].erreur == "code inconnu"

    resultats_outils = client.envois[1]["messages"][2]["content"]
    recherche, lecture, erreur = resultats_outils
    assert recherche["content"][0]["text"].startswith("[P3] 03.04")
    assert "renvois : 05.03" in recherche["content"][0]["text"]
    assert recherche["content"][-1]["text"] == "Déjà fournis plus haut : P1."
    assert erreur["is_error"] is True


def test_selection_vide():
    client = FauxClient(_message(_outil("retenir_passages", {"etiquettes": [], "justification": "hors sujet"})))
    selection = _agent(client).chercher("Recette des crêpes ?")
    assert selection.fin == "retenue" and selection.passages == ()  # abstention décidée par l'agent


def test_etiquettes_toutes_inconnues_repli():
    client = FauxClient(_message(_outil("retenir_passages", {"etiquettes": ["P9"], "justification": "?"})))
    selection = _agent(client).chercher("Q ?")
    assert selection.fin == "repli_etiquettes"
    assert [r.code for r in selection.passages] == ["01.01", "01.02"]  # passages vus


def test_reponse_texte_repli():
    client = FauxClient(_message(NS(type="text", text="Voici la réponse."), stop_reason="end_turn"))
    selection = _agent(client).chercher("Q ?")
    assert selection.fin == "repli_texte"
    assert [r.code for r in selection.passages] == ["01.01", "01.02"]


def test_budget_epuise():
    client = FauxClient(*[_message(_outil("rechercher_regles", {"requete": f"r{i}"}, str(i))) for i in range(3)])
    source = FausseSource()
    selection = _agent(client, source, max_tours=3).chercher("Q ?")
    assert selection.fin == "repli_budget" and selection.tours == 3
    assert [q for q, _ in source.recherches] == ["Q ?", "r0", "r1"]  # rien d'exécuté au dernier tour
    rappel = client.envois[2]["messages"][-1]["content"][-1]
    assert rappel == {"type": "text", "text": RAPPEL_BUDGET}


def test_limite_de_passages():
    client = FauxClient(_message(_outil("retenir_passages", {"etiquettes": ["P1", "P2"], "justification": "."})))
    assert len(_agent(client, max_passages=1, min_passages=1).chercher("Q ?").passages) == 1


# --------------------------------------------------------------------------- #
# Complément (min_passages)
# --------------------------------------------------------------------------- #
def _recherche_puis_selection(etiquettes):
    """Amorce (P1 01.01, P2 01.02), recherche AVANCER (P3 03.04), lecture 05.03 (P4), puis sélection."""
    return FauxClient(
        _message(_outil("rechercher_regles", {"requete": "AVANCER"}), _outil("lire_regle", {"code": "05.03"})),
        _message(_outil("retenir_passages", {"etiquettes": etiquettes, "justification": "."})),
    )


def test_complement_apres_les_choix_de_l_agent():
    etapes = []
    selection = _agent(_recherche_puis_selection(["P3"]), min_passages=3).chercher("Q ?", au_fil=etapes.append)
    assert [r.code for r in selection.passages] == ["03.04", "01.01", "01.02"]  # choix de l'agent, puis ordre d'arrivée
    assert selection.nb_complement == 2
    assert [r.code for r in selection.retenus_par_agent] == ["03.04"]
    assert etapes[-1] == AppelOutil("complement", "minimum 3 passages", ("01.01", "01.02"))


def test_complement_borne_par_les_passages_vus():
    selection = _agent(_recherche_puis_selection(["P3"]), min_passages=8).chercher("Q ?")
    assert [r.code for r in selection.passages] == ["03.04", "01.01", "01.02", "05.03"]  # 4 vus seulement
    assert selection.nb_complement == 3


def test_pas_de_complement_si_minimum_atteint():
    selection = _agent(_recherche_puis_selection(["P4", "P2", "P1"]), min_passages=3).chercher("Q ?")
    assert [r.code for r in selection.passages] == ["05.03", "01.02", "01.01"]
    assert selection.nb_complement == 0


def test_pas_de_complement_pour_une_selection_vide():
    selection = _agent(_recherche_puis_selection([]), min_passages=5).chercher("Recette des crêpes ?")
    assert selection.passages == () and selection.nb_complement == 0   # l'abstention est préservée


def test_min_passages_zero_desactive_le_complement():
    selection = _agent(_recherche_puis_selection(["P3"]), min_passages=0).chercher("Q ?")
    assert [r.code for r in selection.passages] == ["03.04"]


def test_sans_amorce():
    client = FauxClient(_message(_outil("retenir_passages", {"etiquettes": [], "justification": "."})))
    source = FausseSource()
    _agent(client, source, amorce=False).chercher("Ma question ?")
    assert source.recherches == []
    assert client.envois[0]["messages"][0]["content"] == [{"type": "text", "text": "Question du joueur : Ma question ?"}]


def test_k_par_defaut_et_demande():
    client = FauxClient(
        _message(_outil("rechercher_regles", {"requete": "A"}, "1"), _outil("rechercher_regles", {"requete": "B", "k": 2}, "2")),
        _message(_outil("retenir_passages", {"etiquettes": ["P1"], "justification": "."})),
    )
    source = FausseSource()
    _agent(client, source, k=4).chercher("Q ?")
    assert source.recherches == [("Q ?", 4), ("A", 4), ("B", 2)]


def test_question_vide_refusee():
    client = FauxClient()
    with pytest.raises(ValueError, match="vide"):
        _agent(client).chercher("   ")
    assert client.envois == []
