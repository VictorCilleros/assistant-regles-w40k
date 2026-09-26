"""Tests de l'application complète avec AppTest (Streamlit sans navigateur).

Les ressources lourdes (base, BGE-M3, API) sont remplacées par des faux :
on vérifie l'enchaînement des pages et du chat, pas le rendu visuel.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import assistant_regles.ui

APP = str(Path(assistant_regles.ui.__file__).with_name("app.py"))


@pytest.fixture
def app(ressources_factices):
    return AppTest.from_file(APP, default_timeout=30).run()


def test_page_assistant_accueil(app):
    assert not app.exception
    assert app.title[0].value == "Assistant de règles"
    assert len(app.chat_message) == 1  # message d'accueil


def test_question_reponse_citee(app):
    app.chat_input[0].set_value("Les pistolets peuvent-ils tirer ?").run()
    assert not app.exception
    assert not app.error
    textes = [m.value for m in app.markdown]
    assert any("<sup>[1]</sup>" in t for t in textes), "réponse finale avec notes absente"
    assert any("24.20 PISTOLET" in t for t in textes), "sources absentes"
    assert len(app.session_state.historique) == 2


def test_nouvelle_conversation(app):
    app.chat_input[0].set_value("Question ?").run()
    app.sidebar.button[0].click().run()
    assert app.session_state.historique == []


def test_erreur_affichee_sans_casser_la_page(app, monkeypatch):
    from assistant_regles.ui import ressources

    def en_panne():
        raise RuntimeError("base éteinte")

    monkeypatch.setattr(ressources, "moteur_recherche", en_panne)
    app.chat_input[0].set_value("Question ?").run()
    assert not app.exception
    assert app.error and "Impossible de répondre" in app.error[0].value


# AppTest.switch_page ne gère que les pages définies par un fichier ; nos pages
# sont des fonctions : on exécute chacune comme une mini-application.
def _page_modele():
    from assistant_regles.ui import page_modele
    from assistant_regles.ui.config import charger_config_ui

    page_modele.afficher(charger_config_ui())


def _page_documents():
    from assistant_regles.ui import page_documents
    from assistant_regles.ui.config import charger_config_ui

    page_documents.afficher(charger_config_ui())


def test_page_modele(ressources_factices):
    page = AppTest.from_function(_page_modele).run()
    assert not page.exception
    assert page.title[0].value == "Informations sur le modèle"
    tableau = next(m.value for m in page.markdown if m.value.startswith("| Paramètre"))
    assert "claude-sonnet-5" in tableau and "systeme_vf.md [" in tableau


def test_page_documents(ressources_factices):
    page = AppTest.from_function(_page_documents).run()
    assert not page.exception
    assert page.title[0].value == "Documents de référence"
    assert any("Passages indexés : 207" in c.value for c in page.caption)


def test_page_documents_sans_base(ressources_factices, monkeypatch):
    from assistant_regles.ui import ressources

    def en_panne():
        raise RuntimeError("base éteinte")

    monkeypatch.setattr(ressources, "compter_chunks", en_panne)
    page = AppTest.from_function(_page_documents).run()
    assert not page.exception
    assert any("base indisponible" in c.value for c in page.caption)
