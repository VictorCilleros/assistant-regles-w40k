"""Page « Assistant » : le chat.

Chaque question est traitée indépendamment (baseline) : l'historique est
seulement affiché, il n'est pas transmis au modèle.

Déroulé d'une question : recherche des passages, réponse diffusée en streaming,
puis remplacement du texte diffusé par la version finale avec ses notes de
citation, et affichage des sources.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import streamlit as st

from assistant_regles.rag.config import charger_config as charger_config_rag
from assistant_regles.ui import ressources
from assistant_regles.ui.rendu import (
    legende_technique,
    markdown_question,
    markdown_reponse,
    markdown_sources,
)

if TYPE_CHECKING:
    from assistant_regles.rag.generation import Reponse
    from assistant_regles.ui.config import ConfigUi, Textes

logger = logging.getLogger(__name__)


def _vider_historique() -> None:
    st.session_state.historique = []


def _afficher_details(reponse: Reponse, textes: Textes) -> None:
    """Sources repliables sous la réponse, puis la légende technique."""
    if reponse.citations:
        with st.expander(f"{textes.chat.titre_sources} ({len(reponse.citations)})"):
            st.markdown(markdown_sources(reponse), unsafe_allow_html=True)
    st.caption(legende_technique(reponse))


def _afficher_erreur(textes: Textes, detail: str) -> None:
    st.error(textes.chat.erreur)
    with st.expander(textes.chat.details_erreur):
        st.code(detail, language=None)


def _repondre(question: str, textes: Textes) -> Reponse | str:
    """Recherche puis génération en streaming ; renvoie la réponse ou le détail de l'erreur."""
    import psycopg

    config_rag = charger_config_rag()
    try:
        with st.spinner(textes.chat.recherche_en_cours):
            resultats = ressources.moteur_recherche().rechercher(question, config_rag.recherche.k)
            flux = ressources.generateur(config_rag).generer_en_flux(question, resultats)
        zone = st.empty()
        with zone.container():
            st.write_stream(flux)  # texte au fil de l'eau, sans les notes
        reponse = flux.reponse
        zone.markdown(markdown_reponse(reponse), unsafe_allow_html=True)  # version finale, avec notes
        _afficher_details(reponse, textes)
        return reponse
    except Exception as e:  # frontière de l'interface : afficher l'erreur plutôt que casser la page
        logger.exception("Échec de la réponse à : %s", question)
        if isinstance(e, psycopg.OperationalError):
            ressources.reinitialiser()  # la prochaine question rouvrira une connexion
        detail = f"{type(e).__name__} : {e}"
        _afficher_erreur(textes, detail)
        return detail


def afficher(config: ConfigUi) -> None:
    """Affiche la page de chat."""
    textes, apparence = config.textes, config.apparence
    st.title(textes.application.titre)
    st.caption(textes.application.sous_titre)

    with st.sidebar:
        st.button(textes.chat.nouvelle_conversation, icon=":material/add_comment:",
                  on_click=_vider_historique, use_container_width=True)

    historique = st.session_state.setdefault("historique", [])
    if not historique:
        with st.chat_message("assistant", avatar=apparence.avatar_assistant):
            st.markdown(textes.chat.accueil)

    for entree in historique:
        if entree["role"] == "utilisateur":
            with st.chat_message("user", avatar=apparence.avatar_utilisateur):
                st.markdown(markdown_question(entree["contenu"]), unsafe_allow_html=True)
        elif isinstance(entree["contenu"], str):  # erreur conservée dans l'historique
            with st.chat_message("assistant", avatar=apparence.avatar_assistant):
                _afficher_erreur(textes, entree["contenu"])
        else:
            with st.chat_message("assistant", avatar=apparence.avatar_assistant):
                st.markdown(markdown_reponse(entree["contenu"]), unsafe_allow_html=True)
                _afficher_details(entree["contenu"], textes)

    if question := st.chat_input(textes.chat.placeholder):
        historique.append({"role": "utilisateur", "contenu": question})
        with st.chat_message("user", avatar=apparence.avatar_utilisateur):
            st.markdown(markdown_question(question), unsafe_allow_html=True)
        with st.chat_message("assistant", avatar=apparence.avatar_assistant):
            historique.append({"role": "assistant", "contenu": _repondre(question, textes)})
