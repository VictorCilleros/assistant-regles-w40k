"""Page « Assistant » : le chat.

Chaque question est traitée indépendamment (baseline) : l'historique est
seulement affiché, il n'est pas transmis au modèle.

Déroulé d'une question :

1. **recherche** : en mode agent, l'agent choisit les passages et ses étapes
   s'affichent en direct ; sinon, top-k de la question brute ;
2. **réponse** diffusée en streaming, puis remplacée par la version finale avec
   ses notes de citation, et les sources en dessous. Si l'agent n'a retenu
   aucun passage, la phrase d'abstention est affichée sans appeler le générateur.

L'interrupteur « Mode agent » (barre latérale) vaut par défaut ``agent.actif``
de ``rag.yaml`` ; il s'applique aux questions suivantes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import streamlit as st

from assistant_regles.rag.agent import decrire_appel
from assistant_regles.rag.config import charger_config as charger_config_rag
from assistant_regles.ui import ressources
from assistant_regles.ui.rendu import (
    legende_agent,
    legende_technique,
    markdown_question,
    markdown_reponse,
    markdown_sources,
)

if TYPE_CHECKING:
    from assistant_regles.rag.agent import Selection
    from assistant_regles.rag.generation import Reponse
    from assistant_regles.ui.config import ConfigUi, Textes

logger = logging.getLogger(__name__)


def _vider_historique() -> None:
    st.session_state.historique = []


def _titre_recherche(selection: Selection, textes: Textes) -> str:
    return textes.chat.recherche_terminee.format(passages=len(selection.passages), tours=selection.tours)


def _afficher_trace(selection: Selection, textes: Textes) -> None:
    """Étapes de l'agent, repliées (réaffichage de l'historique)."""
    with st.expander(_titre_recherche(selection, textes), icon=":material/travel_explore:"):
        for appel in selection.appels:
            st.markdown(f"- {decrire_appel(appel)}")


def _afficher_details(reponse: Reponse, selection: Selection | None, textes: Textes) -> None:
    """Sources repliables sous la réponse, puis les légendes techniques."""
    if reponse.citations:
        with st.expander(f"{textes.chat.titre_sources} ({len(reponse.citations)})"):
            st.markdown(markdown_sources(reponse), unsafe_allow_html=True)
    st.caption(legende_technique(reponse))
    if selection is not None:
        st.caption(legende_agent(selection))


def _afficher_erreur(textes: Textes, detail: str) -> None:
    st.error(textes.chat.erreur)
    with st.expander(textes.chat.details_erreur):
        st.code(detail, language=None)


def _chercher(question: str, mode_agent: bool, config_rag: Any, textes: Textes):
    """Étape 1 : renvoie (passages, sélection de l'agent ou None)."""
    if not mode_agent:
        with st.spinner(textes.chat.recherche_en_cours):
            return ressources.moteur_recherche().rechercher(question, config_rag.recherche.k), None
    with st.status(textes.chat.recherche_agent, expanded=True) as statut:
        selection = ressources.agent(config_rag).chercher(
            question, au_fil=lambda appel: statut.markdown(f"- {decrire_appel(appel)}")
        )
        statut.update(label=_titre_recherche(selection, textes), state="complete", expanded=False)
    return list(selection.passages), selection


def _repondre(question: str, mode_agent: bool, textes: Textes) -> dict[str, Any] | str:
    """Recherche puis génération ; renvoie {reponse, selection} ou le détail de l'erreur."""
    import psycopg

    config_rag = charger_config_rag()
    try:
        passages, selection = _chercher(question, mode_agent, config_rag, textes)
        generateur = ressources.generateur(config_rag)
        if passages:
            flux = generateur.generer_en_flux(question, passages)
            zone = st.empty()
            with zone.container():
                st.write_stream(flux)  # texte au fil de l'eau, sans les notes
            reponse = flux.reponse
            zone.markdown(markdown_reponse(reponse), unsafe_allow_html=True)  # version finale, avec notes
        else:  # l'agent n'a retenu aucun passage : abstention directe
            reponse = generateur.abstention(question)
            st.markdown(markdown_reponse(reponse), unsafe_allow_html=True)
        _afficher_details(reponse, selection, textes)
        return {"reponse": reponse, "selection": selection}
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
        mode_agent = st.toggle(textes.chat.mode_agent, value=charger_config_rag().agent.actif,
                               key="mode_agent", help=textes.chat.aide_mode_agent)
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
            continue
        with st.chat_message("assistant", avatar=apparence.avatar_assistant):
            if isinstance(entree["contenu"], str):  # erreur conservée dans l'historique
                _afficher_erreur(textes, entree["contenu"])
                continue
            reponse, selection = entree["contenu"]["reponse"], entree["contenu"]["selection"]
            if selection is not None:
                _afficher_trace(selection, textes)
            st.markdown(markdown_reponse(reponse), unsafe_allow_html=True)
            _afficher_details(reponse, selection, textes)

    if question := st.chat_input(textes.chat.placeholder):
        historique.append({"role": "utilisateur", "contenu": question})
        with st.chat_message("user", avatar=apparence.avatar_utilisateur):
            st.markdown(markdown_question(question), unsafe_allow_html=True)
        with st.chat_message("assistant", avatar=apparence.avatar_assistant):
            historique.append({"role": "assistant", "contenu": _repondre(question, mode_agent, textes)})
