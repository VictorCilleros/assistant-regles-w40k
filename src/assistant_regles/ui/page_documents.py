"""Page « Documents de référence » : registre du corpus (config/ui/documents.yaml)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import streamlit as st

from assistant_regles.ui import ressources

if TYPE_CHECKING:
    from assistant_regles.ui.config import ConfigUi

logger = logging.getLogger(__name__)


def afficher(config: ConfigUi) -> None:
    """Une carte par document : nom, édition, version, description, passages indexés, lien."""
    textes = config.textes.documents
    st.title(textes.titre)
    st.markdown(textes.introduction)

    try:
        comptes = ressources.compter_chunks()
    except Exception:  # la page reste utile sans la base : on affiche le reste
        logger.warning("Comptage des passages impossible", exc_info=True)
        comptes = None

    for doc in config.documents:
        with st.container(border=True):
            st.subheader(doc.nom)
            details = [("Édition", doc.edition), ("Version", doc.version),
                       ("Langue", doc.langue), ("Éditeur", doc.editeur)]
            st.markdown(" · ".join(f"**{nom}** {valeur}" for nom, valeur in details if valeur))
            if doc.description:
                st.markdown(doc.description)
            nb = textes.base_indisponible if comptes is None else comptes.get((doc.source, doc.edition), 0)
            st.caption(f"{textes.libelle_chunks} : {nb}")
            st.link_button(textes.bouton_lien, doc.lien, icon=":material/open_in_new:")
