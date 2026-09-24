"""Nettoyage : normalisation du texte et annotation des motifs d'exclusion.

Principe : **marquer avant de supprimer**. Rien n'est retiré ici ; chaque élément
reçoit au plus un motif d'exclusion (la première règle qui s'applique, de la plus
sûre à la plus heuristique). Le texte d'origine est conservé dans ``texte_brut``.
L'audit (``rapport_nettoyage``) et le filtrage (``contenu_conserve``) viennent après.

En cas de doute, une règle conserve l'élément : garder un peu de bruit coûte peu,
retirer une phrase de règle crée un trou que ni le retrieval ni le LLM ne comblent.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from assistant_regles.ingest.config import ParamsNettoyage
from assistant_regles.ingest.texte import normaliser_texte

logger = logging.getLogger(__name__)

CHAPITRE_LIMINAIRE = "LIMINAIRE"
MOTIF_CODE_CITE = re.compile(r"\b\d{2}\.\d{2}\b")
MOTIF_COUT_ISOLE = re.compile(r"\d+\s*PC")
MOTIF_RENVOI_CODE = re.compile(r"\d{2}\.\d{2}$")
PREFIXE_VOIR_AUSSI = "VOIR AUSSI"
SOUS_PARTIE_STRATAGEME = "STRATAGÈME DE BASE"
DEBUT_REGLE_STRATAGEME = ("QUAND", "CIBLE", "EFFET", "RESTRICTIONS")
PONCTUATION_FINALE = (".", ":", ";", "!", "?")


def ressemble_a_une_etiquette(texte: str) -> bool:
    """Forme d'une étiquette de schéma plutôt que d'un fragment de phrase.

    Étiquette : tout en majuscules (ou sans lettres), ou au plus 3 mots sans
    ponctuation finale. « Chaque figurine de cette » (fragment) n'en est pas une.

    Args:
        texte: texte normalisé.

    Returns:
        True si le texte ressemble à une étiquette.
    """
    return texte.upper() == texte or (
        len(texte.split()) <= 3 and not texte.endswith(PONCTUATION_FINALE)
    )


def motif_ligne(ligne: pd.Series | dict, params: ParamsNettoyage) -> str | None:
    """Motif d'exclusion d'un élément (première règle qui s'applique), ou None.

    Args:
        ligne: élément, texte déjà normalisé.
        params: seuils et zones de nettoyage.

    Returns:
        Nom du motif, ou None si l'élément est conservé.
    """
    t = ligne["texte"]
    page = ligne["page"]

    hors_regles = ligne["section_titre"] in params.zones_hors_regles
    if ligne["chapitre"] == CHAPITRE_LIMINAIRE or hors_regles:
        return "zone_hors_regles"
    if not any(c.isalnum() for c in t):  # « ½" » compte comme alphanumérique
        return "sans_contenu"
    if re.fullmatch(r"\d{2}", t) and t == ligne["section_num"]:
        return "numero_section"
    if t.isdigit() and pd.notna(page) and abs(int(t) - page) <= 1:  # ±1 : doubles pages
        return "numero_page"
    if MOTIF_COUT_ISOLE.fullmatch(t):
        return "cout_isole"

    zone_voir_aussi = str(ligne["sous_partie"]).startswith(PREFIXE_VOIR_AUSSI)
    renvoi_code = t.startswith("- ") and MOTIF_RENVOI_CODE.search(t)
    if renvoi_code or (zone_voir_aussi and len(t) <= params.longueur_max_renvoi):
        return "renvoi"

    if (
        ligne["dans_image"]
        and ligne["type_contenu"] != "table"
        and len(t) < params.longueur_max_etiquette
        and ":" not in t
        and ressemble_a_une_etiquette(t)
    ):
        return "etiquette_image"
    return None


def annoter_nettoyage(df: pd.DataFrame, params: ParamsNettoyage) -> pd.DataFrame:
    """Normalise le texte et annote chaque élément avec un éventuel motif d'exclusion.

    Rien n'est supprimé. Colonnes ajoutées : ``texte_brut`` (texte d'origine),
    ``texte`` (normalisé), ``codes_cites`` (codes « XX.YY » mentionnés),
    ``motif_exclusion`` et ``a_relire`` (signal pour les éléments courts conservés).
    Idempotent : peut être relancé sur un DataFrame déjà annoté.

    Args:
        df: éléments issus de ``parse.extraire_elements``.
        params: seuils et zones de nettoyage.

    Returns:
        Copie annotée de ``df``.
    """
    df = df.copy()
    if "texte_brut" not in df:
        df["texte_brut"] = df["texte"]
    df["texte"] = [
        normaliser_texte(t, est_tableau=(tc == "table"))
        for t, tc in zip(df["texte_brut"], df["type_contenu"], strict=True)
    ]
    df["codes_cites"] = df["texte"].map(MOTIF_CODE_CITE.findall)

    # Règles élément par élément
    df["motif_exclusion"] = pd.Series(
        [motif_ligne(ligne, params) for ligne in df.to_dict("records")],
        index=df.index, dtype="object",
    )

    # Lore des stratagèmes : texte avant la première ligne QUAND/CIBLE/… de chaque stratagème
    dans_strat = df["sous_partie"].eq(SOUS_PARTIE_STRATAGEME) & df["motif_exclusion"].isna()
    debut_regle = df["texte"].str.startswith(DEBUT_REGLE_STRATAGEME) & dans_strat
    regle_commencee = debut_regle.groupby(df["code"].fillna("_")).cummax().astype(bool)
    lore = dans_strat & ~regle_commencee & df["type_contenu"].eq("text")
    df.loc[lore, "motif_exclusion"] = "lore_stratageme"

    # Doublons : même code et même texte, sur une autre page que la 1re occurrence.
    # Les répétitions sur une même page (logigrammes, Q/R, tableaux) sont légitimes.
    cle = ["code", "texte"]
    premiere_page = df.groupby(cle, dropna=False)["page"].transform("min")
    doublon = (
        df.duplicated(subset=cle, keep="first")
        & df["page"].ne(premiere_page)
        & df["motif_exclusion"].isna()
    )
    df.loc[doublon, "motif_exclusion"] = "doublon"

    df["a_relire"] = df["motif_exclusion"].isna() & (df["texte"].str.len() < params.seuil_a_relire)
    return df


def rapport_nettoyage(df: pd.DataFrame) -> pd.Series:
    """Nombre d'éléments par motif d'exclusion (« conserve » pour les éléments gardés).

    Args:
        df: éléments annotés.

    Returns:
        Série motif -> nombre d'éléments.
    """
    return df["motif_exclusion"].fillna("conserve").value_counts()


def contenu_conserve(df: pd.DataFrame) -> pd.DataFrame:
    """Éléments sans motif d'exclusion, dans l'ordre de lecture.

    Args:
        df: éléments annotés.

    Returns:
        Éléments conservés, triés par ``ordre``.
    """
    return df[df["motif_exclusion"].isna()].sort_values("ordre")
