"""Tests de la génération avec un faux client Anthropic (ni réseau, ni clé, ni coût).

Le faux client renvoie des réponses au format documenté de l'API Messages
(blocs thinking / text, citations search_result_location).
"""

import uuid
from types import SimpleNamespace as NS

import pytest

from assistant_regles.rag.config import ParamsGeneration
from assistant_regles.rag.generation import (
    Generateur,
    PromptSysteme,
    charger_prompt,
    construire_message,
    decouper,
    est_abstention,
    formater_reponse,
    titre_resultat,
    verifier_prompt,
    vers_search_result,
)
from assistant_regles.rag.recherche import Resultat

PHRASE = "Je ne trouve pas la réponse dans les extraits du livre de règles fournis."


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #
def _resultat(i=0, texte="PREFIXE > 01 Section > 01.01 TITRE\n\nPremier paragraphe.\n\n\n\nSecond paragraphe."):
    return Resultat(
        rang=i + 1, score=0.6, id=uuid.uuid4(), code=f"01.0{i}", chapitre="CHAPITRE",
        section_num="01", section_titre="Section", sous_section=f"TITRE {i}",
        page_debut=10 + i, page_fin=10 + i, type_contenu="regle", source="livre",
        edition="11e", codes_cites=[], texte=texte,
    )


def _citation(index, debut=0, fin=1, texte="texte cité"):
    return NS(type="search_result_location", search_result_index=index, start_block_index=debut,
              end_block_index=fin, cited_text=texte, title=f"titre {index}", source=f"chunk:{index}")


def _message(blocs, stop_reason="end_turn"):
    return NS(content=blocs, stop_reason=stop_reason, usage=NS(input_tokens=2500, output_tokens=300))


class FauxFlux:
    """Imite le gestionnaire de contexte renvoyé par ``messages.stream``."""

    def __init__(self, message, morceaux):
        self._message = message
        self.text_stream = iter(morceaux)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FauxClient:
    """Imite ``anthropic.Anthropic`` : enregistre les appels, renvoie un message préparé."""

    def __init__(self, message, morceaux=()):
        self.appels = []
        self.messages = NS(create=self._create, stream=self._stream)
        self._message = message
        self._morceaux = list(morceaux)

    def _create(self, **kwargs):
        self.appels.append(kwargs)
        return self._message

    def _stream(self, **kwargs):
        self.appels.append(kwargs)
        return FauxFlux(self._message, self._morceaux)


@pytest.fixture
def prompt():
    texte = f"Prompt de test.\nSi besoin, commence par : « {PHRASE} »"
    return PromptSysteme("test.md", texte, "a" * 64)


@pytest.fixture
def params():
    return ParamsGeneration(phrase_abstention=PHRASE)


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def test_prompt_du_repo_charge_et_coherent():
    """systeme_vf.md existe et contient la phrase d'abstention de la config par défaut."""
    prompt = charger_prompt("systeme_vf.md", ParamsGeneration().phrase_abstention)
    assert prompt.texte.strip()
    assert len(prompt.empreinte) == 64


def test_prompt_absent_liste_les_disponibles():
    with pytest.raises(FileNotFoundError, match="systeme_vf.md"):
        charger_prompt("inexistant.md", PHRASE)


def test_prompt_sans_phrase_abstention_refuse():
    with pytest.raises(ValueError, match="abstention"):
        verifier_prompt("Un prompt qui a oublié la consigne.", PHRASE)


# --------------------------------------------------------------------------- #
# Construction du message
# --------------------------------------------------------------------------- #
def test_decouper_par_paragraphe_ecarte_les_vides():
    assert decouper("A\n\n\n\nB\n\n  \n\nC", "paragraphe") == ["A", "B", "C"]
    assert decouper("A\n\nB", "chunk") == ["A\n\nB"]


def test_titre_resultat():
    assert titre_resultat(_resultat()) == "01.00 TITRE 0 — p. 10"


@pytest.mark.parametrize(("granularite", "nb_blocs"), [("chunk", 1), ("paragraphe", 3)])
def test_vers_search_result(granularite, nb_blocs):
    r = _resultat()
    bloc = vers_search_result(r, granularite)
    assert bloc["type"] == "search_result"
    assert bloc["source"] == f"chunk:{r.id}"
    assert bloc["citations"] == {"enabled": True}
    assert len(bloc["content"]) == nb_blocs
    assert all(b["type"] == "text" and b["text"] for b in bloc["content"])


def test_message_extraits_puis_question():
    message = construire_message("Ma question ?", [_resultat(0), _resultat(1)], "chunk")
    types = [b["type"] for b in message["content"]]
    assert message["role"] == "user"
    assert types == ["search_result", "search_result", "text"]
    assert message["content"][-1]["text"] == "Ma question ?"


@pytest.mark.parametrize(("reflexion", "thinking_attendu"), [(True, None), (False, {"type": "disabled"})])
def test_parametres_requete(prompt, reflexion, thinking_attendu):
    params = ParamsGeneration(phrase_abstention=PHRASE, reflexion=reflexion, effort="medium")
    parametres = Generateur(None, params, prompt).parametres_requete("Q ?", [_resultat()])
    assert parametres["model"] == "claude-sonnet-5"
    assert parametres["system"] == prompt.texte
    assert parametres["output_config"] == {"effort": "medium"}
    assert parametres.get("thinking") == thinking_attendu
    assert "temperature" not in parametres  # refusée par Claude Sonnet 5


# --------------------------------------------------------------------------- #
# Lecture de la réponse
# --------------------------------------------------------------------------- #
def test_generer_relie_les_citations_aux_chunks(prompt, params):
    resultats = [_resultat(0), _resultat(1), _resultat(2)]
    message = _message([
        NS(type="thinking", thinking="réflexion interne"),
        NS(type="text", text="Oui.", citations=None),
        NS(type="text", text=" Première règle", citations=[_citation(0)]),
        NS(type="text", text=", et seconde règle.", citations=[_citation(0), _citation(2, 1, 2)]),
    ])
    reponse = Generateur(FauxClient(message), params, prompt).generer("Q ?", resultats)

    assert reponse.texte == "Oui. Première règle, et seconde règle."  # réflexion ignorée
    assert [(c.rang_resultat, c.code) for c in reponse.citations] == [(1, "01.00"), (3, "01.02")]
    assert reponse.abstention is False
    assert reponse.part_citee == pytest.approx(34 / 38)
    assert (reponse.prompt, reponse.empreinte_prompt) == ("test.md", "a" * 64)
    assert (reponse.tokens_entree, reponse.tokens_sortie) == (2500, 300)


def test_citation_vers_resultat_inexistant_refusee(prompt, params):
    message = _message([NS(type="text", text="x", citations=[_citation(5)])])
    with pytest.raises(ValueError, match="inexistant"):
        Generateur(FauxClient(message), params, prompt).generer("Q ?", [_resultat()])


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        (PHRASE + " Il manque la règle X.", True),
        (f"« {PHRASE} »", True),               # guillemets du prompt recopiés
        (f"\n  {PHRASE}", True),
        (f"Oui. Mais {PHRASE}", False),        # pas au début : pas une abstention
        ("Oui, c'est possible.", False),
    ],
)
def test_est_abstention(texte, attendu):
    assert est_abstention(texte, PHRASE) is attendu


def test_abstention_detectee_dans_la_reponse(prompt, params):
    message = _message([NS(type="text", text=PHRASE, citations=None)])
    reponse = Generateur(FauxClient(message), params, prompt).generer("Recette de crêpes ?", [_resultat()])
    assert reponse.abstention is True
    assert reponse.citations == [] and reponse.part_citee == 0.0


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_arret_anormal_journalise(prompt, params, caplog, stop_reason):
    message = _message([NS(type="text", text="Début", citations=None)], stop_reason=stop_reason)
    reponse = Generateur(FauxClient(message), params, prompt).generer("Q ?", [_resultat()])
    assert reponse.stop_reason == stop_reason
    assert caplog.records and caplog.records[-1].levelname == "WARNING"


@pytest.mark.parametrize(("question", "resultats", "motif"), [("  ", [1], "vide"), ("Q ?", [], "Aucun chunk")])
def test_entrees_invalides_refusees_sans_appel(prompt, params, question, resultats, motif):
    client = FauxClient(_message([]))
    resultats = [_resultat()] if resultats else []
    with pytest.raises(ValueError, match=motif):
        Generateur(client, params, prompt).generer(question, resultats)
    assert client.appels == []


def test_formater_reponse(prompt, params):
    message = _message([
        NS(type="text", text="Oui", citations=[_citation(0, texte="Premier   paragraphe.")]),
        NS(type="text", text=", aussi", citations=[_citation(0)]),  # même citation : même numéro
    ])
    texte = formater_reponse(Generateur(FauxClient(message), params, prompt).generer("Q ?", [_resultat()]))
    assert texte.splitlines()[0] == "Oui [1], aussi [1]"
    assert "[1] titre 0" in texte and "« Premier paragraphe. »" in texte
    assert "prompt test.md [aaaaaaaa]" in texte


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
def test_flux_diffuse_le_texte_puis_fournit_la_reponse(prompt, params):
    message = _message([
        NS(type="thinking", thinking="réflexion"),
        NS(type="text", text="Oui.", citations=None),
        NS(type="text", text=" Règle", citations=[_citation(0)]),
    ])
    client = FauxClient(message, morceaux=["Ou", "i.", " Règle"])
    flux = Generateur(client, params, prompt).generer_en_flux("Q ?", [_resultat()])

    assert client.appels == []          # rien n'est envoyé avant l'itération
    assert flux.reponse is None
    assert "".join(flux) == "Oui. Règle"
    assert flux.reponse.texte == "Oui. Règle"
    assert [c.code for c in flux.reponse.citations] == ["01.00"]
    assert client.appels[0]["output_config"] == {"effort": params.effort}


def test_flux_valide_les_entrees_immediatement(prompt, params):
    with pytest.raises(ValueError, match="vide"):
        Generateur(FauxClient(_message([])), params, prompt).generer_en_flux("  ", [_resultat()])
