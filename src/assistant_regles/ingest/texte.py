"""Normalisation de texte partagée par les étapes d'ingestion.

Deux usages, deux politiques :

- **Titres** (métadonnées de structure) : normalisation NFKC + suppression des
  caractères invisibles, pour fiabiliser la détection des codes « XX.YY ».
- **Corps des règles** : remplacements ciblés et explicites uniquement, **sans NFKC**,
  car NFKC altère des caractères porteurs de sens dans le jeu (« ½ » devient « 1⁄2 »).
  Restent intacts : ½, pouces ", +, ×, « », …, mots-clés en majuscules.
"""

from __future__ import annotations

import re
import unicodedata

# Catégories Unicode supprimées : caractères de contrôle (Cc, ex. \x08 hérité du PDF)
# et de format (Cf, ex. espace de largeur nulle, trait d'union conditionnel).
CATEGORIES_INVISIBLES = frozenset({"Cc", "Cf"})

# Remplacements typographiques ciblés pour le corps du texte.
REMPLACEMENTS = str.maketrans(
    {
        # Ligatures
        "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
        # Apostrophes
        "’": "'", "‘": "'", "ʼ": "'",
        # Guillemets anglais et double prime (pouces) -> guillemet droit
        "“": '"', "”": '"', "„": '"', "″": '"',
    }
)

# Puces converties en tiret Markdown. « ► » est volontairement exclu :
# il marque les renvois et reste distinct des puces de liste.
MOTIF_PUCE = re.compile(r"^[▪▫•●]\s*")

_BLANCS = re.compile(r"\s+")
_BLANCS_SAUF_SAUT_DE_LIGNE = re.compile(r"[^\S\n]+")


def retirer_invisibles(texte: str) -> str:
    """Supprime les caractères de contrôle et de format, en conservant les blancs.

    Les retours à la ligne et tabulations (catégorie Cc) sont conservés pour être
    ensuite réduits en espaces : sans cela, deux mots séparés par un saut de ligne
    seraient collés.

    Args:
        texte: texte brut.

    Returns:
        Texte sans caractères invisibles.
    """
    return "".join(
        c for c in texte if c.isspace() or unicodedata.category(c) not in CATEGORIES_INVISIBLES
    )


def preparer_titre(texte: str) -> str:
    """Prépare un titre pour la détection de structure (NFKC, invisibles, espaces).

    Args:
        texte: texte brut d'un titre Docling.

    Returns:
        Titre nettoyé, utilisé pour la détection et stocké comme métadonnée.
    """
    t = unicodedata.normalize("NFKC", texte)
    t = retirer_invisibles(t)
    return _BLANCS.sub(" ", t).strip()


def normaliser_pour_comparaison(texte: str) -> str:
    """Forme canonique pour comparer des titres : sans accents, minuscules.

    Args:
        texte: texte brut.

    Returns:
        Texte normalisé, destiné uniquement aux comparaisons.
    """
    t = unicodedata.normalize("NFKD", preparer_titre(texte))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.casefold()


def normaliser_texte(texte: str, est_tableau: bool = False) -> str:
    """Normalise le texte d'un élément de contenu, sans NFKC.

    Args:
        texte: texte brut de l'élément.
        est_tableau: True pour un tableau Markdown ; les retours à la ligne
            (séparateurs de lignes du tableau) sont alors préservés.

    Returns:
        Texte normalisé.
    """
    t = retirer_invisibles(texte).translate(REMPLACEMENTS)
    if est_tableau:
        t = _BLANCS_SAUF_SAUT_DE_LIGNE.sub(" ", t)
        return "\n".join(ligne.strip() for ligne in t.split("\n")).strip()
    t = _BLANCS.sub(" ", t).strip()
    return MOTIF_PUCE.sub("- ", t)
