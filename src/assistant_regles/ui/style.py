"""Feuille de style de l'interface (fonctions pures, testables sans Streamlit).

Le thème natif (``.streamlit/config.toml``) gère couleurs de base et polices.
Ce CSS ajoute seulement ce que le thème ne sait pas faire :

- l'image de fond, sous un voile de la couleur de fond pour garder le texte lisible ;
- des messages façon Claude : question de l'utilisateur dans une bulle à droite,
  réponse de l'assistant sans bulle, en pleine largeur ;
- des titres en capitales avec un filet de couleur d'accent ;
- le pied de page fixe.

Les messages de l'utilisateur sont repérés par un marqueur ``.msg-utilisateur``
placé dans leur contenu, plutôt que par les identifiants internes de Streamlit,
qui changent selon le type d'avatar et d'une version à l'autre.
"""

from __future__ import annotations

import base64
import html
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TYPES_IMAGE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
TAILLE_IMAGE_CONSEILLEE = 1_000_000  # octets
MARQUEUR_UTILISATEUR = '<span class="msg-utilisateur"></span>'


def hex_vers_rgb(couleur: str) -> tuple[int, int, int]:
    """« #RRGGBB » -> (r, g, b)."""
    c = couleur.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def image_en_data_uri(chemin: Path | None) -> str | None:
    """Encode l'image en data URI (intégrée au CSS), ou None si absente.

    Raises:
        ValueError: format non pris en charge.
    """
    if chemin is None or not chemin.is_file():
        if chemin is not None:
            logger.warning("Image de fond introuvable : %s (aucune image affichée)", chemin)
        return None
    type_mime = TYPES_IMAGE.get(chemin.suffix.lower())
    if type_mime is None:
        raise ValueError(f"Format d'image non pris en charge : {chemin.suffix} ({sorted(TYPES_IMAGE)})")
    contenu = chemin.read_bytes()
    if len(contenu) > TAILLE_IMAGE_CONSEILLEE:
        logger.warning("Image de fond lourde (%.1f Mo) : la compresser accélère l'affichage", len(contenu) / 1e6)
    return f"data:{type_mime};base64,{base64.b64encode(contenu).decode('ascii')}"


def construire_css(
    couleur_fond: str,
    couleur_secondaire: str,
    couleur_accent: str,
    couleur_bordure: str,
    opacite_image: float,
    image_data_uri: str | None,
) -> str:
    """Construit la feuille de style complète."""
    r, g, b = hex_vers_rgb(couleur_fond)
    r2, g2, b2 = hex_vers_rgb(couleur_secondaire)
    voile = f"rgba({r}, {g}, {b}, {1 - opacite_image:.2f})"
    fond = (
        f"background-image: linear-gradient({voile}, {voile}), url(\"{image_data_uri}\");"
        if image_data_uri else ""
    )
    return f"""
/* Image de fond sous un voile */
[data-testid="stApp"] {{
    {fond}
    background-size: cover;
    background-position: center;
    background-attachment: fixed;
}}
[data-testid="stHeader"], [data-testid="stBottom"] > div {{ background: transparent; }}

/* Titres en capitales, filet d'accent sous le titre principal */
h1 {{ text-transform: uppercase; letter-spacing: 0.08em;
      border-bottom: 2px solid {couleur_accent}; padding-bottom: 0.3rem; }}
h2, h3 {{ letter-spacing: 0.04em; }}

/* Messages façon Claude : réponse sans bulle, question dans une bulle à droite */
[data-testid="stChatMessage"] {{ background: transparent; }}
[data-testid="stChatMessage"]:has(.msg-utilisateur) {{
    flex-direction: row-reverse;
    margin-left: auto;
    max-width: 85%;
    background: rgba({r2}, {g2}, {b2}, 0.92);
    border: 1px solid {couleur_bordure};
    border-radius: 1.1rem;
    padding: 0.6rem 0.9rem;
}}

/* Sources sous la réponse */
.sources-citees blockquote {{ border-left: 3px solid {couleur_accent}; }}

/* Pied de page fixe, au-dessus de la zone du champ de saisie (stBottom) :
   cette zone est elle aussi fixe en bas de page ; sans z-index plus élevé,
   elle recouvre le pied de page et intercepte les clics sur ses liens.
   Le champ de saisie est remonté (padding) pour ne pas être masqué. */
.pied-de-page {{
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000;
    padding: 0.35rem 1rem; text-align: center; font-size: 0.75rem; opacity: 0.9;
    background: rgba({r}, {g}, {b}, 0.94);
    border-top: 1px solid {couleur_accent};
}}
.pied-de-page a {{ margin: 0 0.4rem; }}
[data-testid="stBottom"] > div {{ padding-bottom: 2.2rem; }}
[data-testid="stMainBlockContainer"] {{ padding-bottom: 4rem; }}
"""


def html_pied_de_page(nom: str, email: str, github: str, linkedin: str, mention: str) -> str:
    """HTML du pied de page ; les champs vides sont omis, tout est échappé."""
    liens = [html.escape(nom)]
    if email:
        liens.append(f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>')
    if github:
        liens.append(f'<a href="{html.escape(github)}" target="_blank" rel="noopener noreferrer">GitHub</a>')
    if linkedin:
        liens.append(f'<a href="{html.escape(linkedin)}" target="_blank" rel="noopener noreferrer">LinkedIn</a>')
    ligne_mention = f"<br><span>{html.escape(mention)}</span>" if mention else ""
    return f'<div class="pied-de-page">{" · ".join(liens)}{ligne_mention}</div>'
