"""Point d'entrée de l'interface Streamlit.

Lancement : ``uv run interface-regles`` (depuis n'importe quel dossier du repo).

Streamlit réexécute ce script à chaque interaction : il ne fait donc que
choses légères (lecture des YAML, style, navigation). Les objets coûteux sont
dans ``ressources`` (mis en cache).
"""

from __future__ import annotations

import logging
from functools import partial

import streamlit as st
from dotenv import load_dotenv

from assistant_regles.ingest.config import trouver_racine
from assistant_regles.ui import page_assistant, page_documents, page_modele, ressources
from assistant_regles.ui.config import charger_config_ui
from assistant_regles.ui.style import construire_css, html_pied_de_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
load_dotenv(trouver_racine() / ".env")

config = charger_config_ui()
textes = config.textes
st.set_page_config(page_title=textes.application.titre, page_icon=textes.application.icone, layout="centered")


def _couleur_theme(option: str, defaut: str) -> str:
    """Couleur du thème (.streamlit/config.toml), avec une valeur de repli."""
    return st.get_option(f"theme.{option}") or defaut


css = construire_css(
    couleur_fond=_couleur_theme("backgroundColor", "#0F0E0C"),
    couleur_secondaire=_couleur_theme("secondaryBackgroundColor", "#1C1915"),
    couleur_accent=config.apparence.couleur_accent,
    couleur_bordure=_couleur_theme("borderColor", "#3A3226"),
    opacite_image=config.apparence.opacite_image,
    image_data_uri=ressources.image_fond(config.chemin_image_fond),
)
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

navigation = st.navigation([
    st.Page(partial(page_assistant.afficher, config), title=textes.navigation.assistant,
            icon=":material/forum:", url_path="assistant", default=True),
    st.Page(partial(page_modele.afficher, config), title=textes.navigation.modele,
            icon=":material/memory:", url_path="modele"),
    st.Page(partial(page_documents.afficher, config), title=textes.navigation.documents,
            icon=":material/menu_book:", url_path="documents"),
])
navigation.run()

pied = textes.pied_de_page
st.markdown(html_pied_de_page(pied.nom, pied.email, pied.github, pied.linkedin, pied.mention),
            unsafe_allow_html=True)
