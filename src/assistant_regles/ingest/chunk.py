"""Découpage des éléments conservés en chunks, avec métadonnées.

Unité de base : la **sous-section codée** (« 03.01 »), plus les introductions de
section (éléments sans code). Découpage à trois niveaux :

1. l'unité tient dans ``max_tokens`` -> un seul chunk ;
2. sinon, regroupement de **sous-parties contiguës** jusqu'à ``cible_tokens``
   (une sous-partie seule peut aller jusqu'à ``max_tokens``) — pas de chevauchement,
   le contexte est porté par le préfixe et les titres ;
3. une sous-partie seule > ``max_tokens`` -> découpage aux frontières d'éléments,
   avec chevauchement d'un élément entier (filet de sécurité).

Chaque chunk commence par un préfixe de contexte « Chapitre > NN Section > NN.NN Titre »
et réinsère les titres de sous-parties en Markdown : ce sont les mots des questions.

Le compteur de tokens est **injecté** (``compter_tokens``) : le vrai tokenizer en
production, un compteur simple dans les tests.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from assistant_regles.ingest.config import ParamsChunking, ParamsDocument

logger = logging.getLogger(__name__)

CompteurTokens = Callable[[str], int]

# Espace de noms des identifiants : un même chunk garde le même id d'une exécution
# à l'autre (upsert idempotent en base, résultats d'évaluation comparables).
ESPACE_UUID = uuid.uuid5(uuid.NAMESPACE_URL, "assistant-regles-w40k")
CLES_UNITE = ["chapitre", "section_num", "section_titre", "code", "sous_section"]


class Chunk(BaseModel):
    """Chunk prêt à être embarqué, avec ses métadonnées."""

    id: str
    texte: str
    chapitre: str | None
    section_num: str | None
    section_titre: str | None
    code: str | None
    sous_section: str | None
    sous_parties: list[str]
    page_debut: int | None
    page_fin: int | None
    type_contenu: Literal["regle", "table"]
    edition: str
    source: str
    codes_cites: list[str]          # renvois vers d'autres règles (pour l'agent)
    partie: int                     # rang dans l'unité découpée (à partir de 1)
    nb_parties: int
    nb_tokens: int
    hors_budget: bool
    ordres: list[int]               # traçabilité vers les éléments Docling


# --------------------------------------------------------------------------- #
# Compteur de tokens
# --------------------------------------------------------------------------- #
def charger_compteur_tokens(nom_tokenizer: str) -> CompteurTokens:
    """Charge le tokenizer d'embedding et renvoie une fonction de comptage (mise en cache).

    Seul le tokenizer est téléchargé, pas le modèle.

    Args:
        nom_tokenizer: identifiant Hugging Face (ex. « BAAI/bge-m3 »).

    Returns:
        Fonction texte -> nombre de tokens (sans tokens spéciaux).
    """
    from transformers import AutoTokenizer  # import local : dépendance lourde

    tokenizer = AutoTokenizer.from_pretrained(nom_tokenizer)

    @cache
    def compter(texte: str) -> int:
        return len(tokenizer(texte, add_special_tokens=False)["input_ids"])

    return compter


# --------------------------------------------------------------------------- #
# Rendu du texte
# --------------------------------------------------------------------------- #
def valeur(x):
    """Convertit les valeurs manquantes pandas (NaN, None) en None."""
    return None if pd.isna(x) else x


def construire_prefixe(chapitre, section_num, section_titre, code, sous_section) -> str:
    """Construit le chemin de contexte « Chapitre > NN Section > NN.NN Sous-section ».

    Returns:
        Préfixe, sans les niveaux absents.
    """
    niveaux = [
        chapitre,
        " ".join(p for p in (section_num, section_titre) if p),
        " ".join(p for p in (code, sous_section) if p),
    ]
    return " > ".join(n for n in niveaux if n)


def rendre_unite(elements: pd.DataFrame, prefixe: str) -> str:
    """Reconstruit le texte Markdown d'un groupe d'éléments, titres de sous-parties réinsérés.

    Args:
        elements: éléments triés dans l'ordre de lecture.
        prefixe: chemin de contexte placé en tête.

    Returns:
        Texte tel qu'il sera embarqué.
    """
    lignes, sous_partie_courante = [prefixe, ""], None
    for sp, type_contenu, texte in zip(
        elements["sous_partie"], elements["type_contenu"], elements["texte"], strict=True
    ):
        sp = valeur(sp)
        if sp and sp != sous_partie_courante:
            lignes += ["", f"### {sp}"]
            sous_partie_courante = sp
        lignes += ["", texte, ""] if type_contenu == "table" else [texte]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


# --------------------------------------------------------------------------- #
# Découpage
# --------------------------------------------------------------------------- #
def blocs_sous_parties(elements: pd.DataFrame) -> list[pd.DataFrame]:
    """Découpe une unité en blocs de sous-parties **contiguës**, dans l'ordre de lecture.

    Un groupby sur le titre fusionnerait deux sous-parties homonymes éloignées ;
    on découpe donc à chaque changement de sous-partie.

    Args:
        elements: éléments d'une unité, triés par ordre.

    Returns:
        Blocs ; les éléments sans sous-partie forment leur propre bloc.
    """
    cle = elements["sous_partie"].fillna("∅")
    numero_bloc = (cle != cle.shift()).cumsum()
    return [bloc for _, bloc in elements.groupby(numero_bloc, sort=False)]


def decouper_paragraphes(
    bloc: pd.DataFrame, prefixe: str, params: ParamsChunking, compter_tokens: CompteurTokens
) -> list[pd.DataFrame]:
    """Filet de sécurité : découpe un bloc trop gros aux frontières d'éléments.

    Le dernier élément d'un morceau est répété en tête du suivant (élément entier,
    jamais de coupe en milieu de phrase). Un élément seul hors budget reste entier.

    Args:
        bloc: éléments d'une sous-partie dépassant ``max_tokens``.
        prefixe: chemin de contexte.
        params: budget de découpage.
        compter_tokens: fonction de comptage.

    Returns:
        Morceaux (DataFrames d'éléments).
    """
    def taille(morceau: pd.DataFrame) -> int:
        return compter_tokens(rendre_unite(morceau, prefixe))

    morceaux, courant = [], []
    for i in range(len(bloc)):
        element = bloc.iloc[[i]]
        if taille(element) > params.max_tokens:  # ex. très gros tableau : gardé entier
            if courant:
                morceaux.append(pd.concat(courant))
            morceaux.append(element)
            courant = []
            continue
        if courant and taille(pd.concat(courant + [element])) > params.cible_tokens:
            morceaux.append(pd.concat(courant))
            # Chevauchement seulement si le morceau fermé contient plusieurs éléments
            # (sinon il serait entièrement inclus dans le suivant).
            chevauchement = courant[-1]
            avec_chevauchement = (
                len(courant) > 1
                and taille(pd.concat([chevauchement, element])) <= params.max_tokens
            )
            courant = [chevauchement, element] if avec_chevauchement else [element]
        else:
            courant.append(element)
    if courant:
        morceaux.append(pd.concat(courant))
    return morceaux


def decouper_unite(
    elements: pd.DataFrame, prefixe: str, params: ParamsChunking, compter_tokens: CompteurTokens
) -> list[pd.DataFrame]:
    """Découpe une unité (sous-section ou introduction de section) selon le budget.

    Args:
        elements: éléments de l'unité, triés par ordre.
        prefixe: chemin de contexte.
        params: budget de découpage.
        compter_tokens: fonction de comptage.

    Returns:
        Morceaux (DataFrames d'éléments), dans l'ordre de lecture.
    """
    def taille(morceau: pd.DataFrame) -> int:
        return compter_tokens(rendre_unite(morceau, prefixe))

    if taille(elements) <= params.max_tokens:
        return [elements]

    morceaux, courant = [], []
    for bloc in blocs_sous_parties(elements):
        if taille(bloc) > params.max_tokens:
            if courant:
                morceaux.append(pd.concat(courant))
                courant = []
            morceaux.extend(decouper_paragraphes(bloc, prefixe, params, compter_tokens))
            continue
        if courant and taille(pd.concat(courant + [bloc])) > params.cible_tokens:
            morceaux.append(pd.concat(courant))
            courant = [bloc]
        else:
            courant.append(bloc)
    if courant:
        morceaux.append(pd.concat(courant))
    return morceaux


# --------------------------------------------------------------------------- #
# Construction des chunks
# --------------------------------------------------------------------------- #
def _page(x) -> int | None:
    return None if pd.isna(x) else int(x)


def construire_chunk(
    morceau: pd.DataFrame,
    cles: tuple,
    prefixe: str,
    partie: int,
    nb_parties: int,
    params: ParamsChunking,
    document: ParamsDocument,
    compter_tokens: CompteurTokens,
) -> Chunk:
    """Assemble le texte et les métadonnées d'un chunk.

    Args:
        morceau: éléments du chunk.
        cles: (chapitre, section_num, section_titre, code, sous_section).
        prefixe: chemin de contexte.
        partie: rang du chunk dans son unité (à partir de 1).
        nb_parties: nombre de chunks de l'unité.
        params: budget de découpage.
        document: source et édition.
        compter_tokens: fonction de comptage.

    Returns:
        Le chunk.
    """
    chapitre, section_num, section_titre, code, sous_section = cles
    texte = rendre_unite(morceau, prefixe)
    nb_tokens = compter_tokens(texte)
    ordres = [int(o) for o in morceau["ordre"]]
    codes_cites = sorted({c for liste in morceau["codes_cites"] for c in liste} - {code})
    return Chunk(
        id=str(uuid.uuid5(ESPACE_UUID, f"{document.source}|{ordres[0]}|{ordres[-1]}")),
        texte=texte,
        chapitre=chapitre,
        section_num=section_num,
        section_titre=section_titre,
        code=code,
        sous_section=sous_section,
        sous_parties=list(dict.fromkeys(v for v in morceau["sous_partie"] if pd.notna(v))),
        page_debut=_page(morceau["page"].min()),
        page_fin=_page(morceau["page"].max()),
        type_contenu="table" if (morceau["type_contenu"] == "table").all() else "regle",
        edition=document.edition,
        source=document.source,
        codes_cites=codes_cites,
        partie=partie,
        nb_parties=nb_parties,
        nb_tokens=nb_tokens,
        hors_budget=nb_tokens > params.max_tokens,
        ordres=ordres,
    )


def construire_chunks(
    df_contenu: pd.DataFrame,
    params: ParamsChunking,
    document: ParamsDocument,
    compter_tokens: CompteurTokens,
) -> list[Chunk]:
    """Découpe les éléments conservés en chunks.

    Args:
        df_contenu: éléments conservés (sans motif d'exclusion), triés par ordre.
        params: budget de découpage.
        document: source et édition.
        compter_tokens: fonction de comptage.

    Returns:
        Chunks dans l'ordre du livre.
    """
    chunks: list[Chunk] = []
    for cles, groupe in df_contenu.groupby(CLES_UNITE, dropna=False, sort=False):
        cles = tuple(valeur(c) for c in cles)
        prefixe = construire_prefixe(*cles)
        morceaux = decouper_unite(groupe, prefixe, params, compter_tokens)
        chunks += [
            construire_chunk(m, cles, prefixe, i, len(morceaux), params, document, compter_tokens)
            for i, m in enumerate(morceaux, start=1)
        ]

    nb_decoupes = sum(1 for c in chunks if c.partie == 1 and c.nb_parties > 1)
    nb_hors_budget = sum(c.hors_budget for c in chunks)
    logger.info("%d chunks (%d unités découpées, %d hors budget)",
                len(chunks), nb_decoupes, nb_hors_budget)
    return chunks


# --------------------------------------------------------------------------- #
# Entrées / sorties
# --------------------------------------------------------------------------- #
def ecrire_jsonl(chunks: Iterable[Chunk], chemin: Path) -> Path:
    """Écrit les chunks en JSONL (un chunk JSON par ligne, UTF-8).

    Args:
        chunks: chunks à écrire.
        chemin: fichier de sortie.

    Returns:
        Le chemin écrit.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")
    return chemin


def lire_jsonl(chemin: Path) -> list[Chunk]:
    """Relit un fichier JSONL de chunks (validés par le modèle).

    Args:
        chemin: fichier JSONL.

    Returns:
        Chunks.
    """
    with chemin.open(encoding="utf-8") as f:
        return [Chunk.model_validate_json(ligne) for ligne in f if ligne.strip()]
