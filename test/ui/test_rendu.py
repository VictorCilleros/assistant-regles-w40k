"""Tests de la mise en forme des réponses."""

from assistant_regles.rag.config import charger_config
from assistant_regles.rag.generation import PromptSysteme
from assistant_regles.ui.rendu import (
    echapper_markdown,
    legende_technique,
    markdown_question,
    markdown_reponse,
    markdown_sources,
    tableau_configuration,
)
from assistant_regles.ui.style import MARQUEUR_UTILISATEUR


def test_echapper_dollar():
    assert echapper_markdown("1$ et 2$") == r"1\$ et 2\$"


def test_reponse_avec_notes_en_exposant(reponse_factice):
    assert markdown_reponse(reponse_factice) == r"Oui. Un PISTOLET peut tirer (\$1\$ fois)<sup>[1]</sup>"


def test_sources(reponse_factice):
    sources = markdown_sources(reponse_factice)
    assert "**[1] 24.20 PISTOLET — p. 57**" in sources
    assert "> Paragraphe un." in sources


def test_legende(reponse_factice):
    legende = legende_technique(reponse_factice)
    assert "prompt systeme_test.md [bbbbbbbb]" in legende and "2400 → 120 tokens" in legende


def test_question_marquee_et_echappee():
    rendu = markdown_question("<script>alert(1)</script> ?")
    assert rendu.startswith(MARQUEUR_UTILISATEUR)
    assert "<script>" not in rendu


def test_tableau_configuration():
    lignes = dict(tableau_configuration(charger_config(), PromptSysteme("p.md", "x", "c" * 64)))
    assert lignes["Prompt système"].endswith("[cccccccc]")
    assert lignes["Granularité des citations"] == "paragraphe"
