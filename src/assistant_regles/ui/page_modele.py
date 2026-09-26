"""Page « Informations sur le modèle » : texte éditable + configuration active."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from assistant_regles.rag.config import charger_config as charger_config_rag
from assistant_regles.rag.generation import charger_prompt
from assistant_regles.ui.rendu import tableau_configuration

if TYPE_CHECKING:
    from assistant_regles.ui.config import ConfigUi


def afficher(config: ConfigUi) -> None:
    """Affiche le texte de config/ui/informations_modele.md puis la configuration lue dans rag.yaml."""
    textes = config.textes
    st.title(textes.navigation.modele)
    st.markdown(config.informations_modele)

    st.subheader(textes.modele.titre_configuration)
    st.caption(textes.modele.intro_configuration)
    config_rag = charger_config_rag()
    try:
        prompt = charger_prompt(config_rag.generation.prompt_systeme, config_rag.generation.phrase_abstention)
    except (FileNotFoundError, ValueError) as e:
        prompt = None
        st.warning(str(e))
    lignes = ["| Paramètre | Valeur |", "|---|---|"]
    lignes += [f"| {nom} | {valeur} |" for nom, valeur in tableau_configuration(config_rag, prompt)]
    st.markdown("\n".join(lignes))
