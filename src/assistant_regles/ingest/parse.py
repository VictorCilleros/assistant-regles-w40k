"""Conversion du PDF (Docling, avec cache) et attribution de la structure.

Étapes :

1. ``convertir_pdf`` : PDF -> DoclingDocument, mis en cache en JSON (la conversion
   est l'étape lente ; on ne la refait que si nécessaire).
2. ``extraire_elements`` : parcours du document dans l'ordre de lecture. Chaque
   élément de contenu (texte, liste, tableau) reçoit son adresse dans le livre :
   chapitre et section (déduits de la page), sous-section codée « XX.YY » et
   sous-partie (déduites des titres rencontrés).
3. ``diagnostiquer_structure`` : contrôles de cohérence (codes / pages, continuité).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from docling_core.types.doc import DocItemLabel, DoclingDocument, TableItem, TextItem

from assistant_regles.ingest.config import StructureLivre
from assistant_regles.ingest.texte import normaliser_pour_comparaison, preparer_titre

logger = logging.getLogger(__name__)

# « 1. NOM DE LA FICHE TECHNIQUE 02.01 » ou « RELANCE DE COMMANDEMENT 15.02 1PC »
# -> numéro d'ordre optionnel, titre, section, sous-section, coût de stratagème optionnel.
MOTIF_CODE = re.compile(
    r"^(?:\d+\.\s+)?(?P<titre>.+?)\s+(?P<sec>\d{2})\.(?P<sous>\d{2})"
    r"(?:\s+(?P<cout>\d+\s*PC))?$"
)
LABELS_TITRE = frozenset({DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER})

CHAPITRE_LIMINAIRE = "LIMINAIRE"
CHAPITRE_HORS_SOMMAIRE = "HORS SOMMAIRE"
SECTION_OUVERTURE = "(ouverture de chapitre)"

COLONNES_ELEMENTS = [
    "ordre", "chapitre", "section_num", "section_titre", "code", "sous_section",
    "sous_partie", "page", "type_contenu", "dans_image", "texte",
]
COLONNES_REPERES = ["ordre", "page", "type", "titre", "code", "section_num_page", "origine"]


# --------------------------------------------------------------------------- #
# 1. Conversion
# --------------------------------------------------------------------------- #
def convertir_pdf(
    chemin_pdf: Path,
    chemin_cache: Path,
    ocr: bool = False,
    structure_tableaux: bool = True,
    forcer: bool = False,
) -> DoclingDocument:
    """Convertit le PDF avec Docling, ou recharge la conversion mise en cache.

    Args:
        chemin_pdf: PDF source.
        chemin_cache: fichier JSON de cache de la conversion.
        ocr: active l'OCR (inutile pour un PDF natif).
        structure_tableaux: active la reconnaissance de structure des tableaux.
        forcer: reconvertit même si le cache existe.

    Returns:
        Le document Docling.

    Raises:
        FileNotFoundError: pas de cache et PDF introuvable.
    """
    if chemin_cache.exists() and not forcer:
        logger.info("Conversion Docling rechargée depuis le cache %s", chemin_cache.name)
        return DoclingDocument.load_from_json(chemin_cache)

    if not chemin_pdf.exists():
        raise FileNotFoundError(f"PDF introuvable : {chemin_pdf}")

    # Import local : Docling (et PyTorch) n'est chargé que si une conversion est nécessaire.
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(do_ocr=ocr, do_table_structure=structure_tableaux)
    convertisseur = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    logger.info("Conversion Docling de %s (plusieurs minutes)…", chemin_pdf.name)
    debut = time.perf_counter()
    doc = convertisseur.convert(chemin_pdf).document
    logger.info("Conversion terminée en %.0f s", time.perf_counter() - debut)

    chemin_cache.parent.mkdir(parents=True, exist_ok=True)
    doc.save_as_json(chemin_cache)
    return doc


# --------------------------------------------------------------------------- #
# 2. Analyse des titres et localisation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReferentielTitres:
    """Titres de chapitres et de sections attendus, sous forme comparable."""

    chapitres: frozenset[str] = frozenset()
    sections: frozenset[str] = frozenset()

    @classmethod
    def depuis_structure(cls, structure: StructureLivre) -> ReferentielTitres:
        """Construit le référentiel à partir de la structure du livre."""
        return cls(
            chapitres=frozenset(normaliser_pour_comparaison(c.titre) for c in structure.chapitres),
            sections=frozenset(
                normaliser_pour_comparaison(s.titre)
                for c in structure.chapitres
                for s in c.sections
            ),
        )


def analyser_titre(
    texte: str,
    section_num: str | None = None,
    referentiel: ReferentielTitres | None = None,
) -> dict:
    """Classe un titre détecté par Docling.

    Args:
        texte: texte brut du titre.
        section_num: section déduite de la page ; un code d'une autre section est un
            renvoi (garde-fou contre les références mises en forme comme des titres).
        referentiel: titres de chapitres / sections attendus.

    Returns:
        dict avec ``type`` (sous_section | renvoi_suspect | pseudo_titre |
        titre_chapitre | titre_section | sous_partie), ``titre`` et ``code``.
    """
    referentiel = referentiel or ReferentielTitres()
    t = preparer_titre(texte)

    if m := MOTIF_CODE.match(t):
        code = f"{m['sec']}.{m['sous']}"
        if section_num is not None and m["sec"] != section_num:
            return {"type": "renvoi_suspect", "titre": t, "code": code}
        return {"type": "sous_section", "titre": m["titre"], "code": code}
    if t.endswith(":"):
        return {"type": "pseudo_titre", "titre": t, "code": None}

    n = re.sub(r"^\d+\s*", "", normaliser_pour_comparaison(t))  # tolère « 07 Titre »
    if n in referentiel.chapitres:
        return {"type": "titre_chapitre", "titre": t, "code": None}
    if n in referentiel.sections:
        return {"type": "titre_section", "titre": t, "code": None}
    return {"type": "sous_partie", "titre": t, "code": None}


def localiser(page: int | None, structure: StructureLivre) -> dict:
    """Déduit chapitre et section à partir du numéro de page.

    Args:
        page: numéro de page Docling (1-indexé), ou None.
        structure: structure du livre.

    Returns:
        dict avec ``chapitre``, ``section_num`` et ``section_titre``.
    """
    vide = {"chapitre": None, "section_num": None, "section_titre": None}
    if page is None:
        return vide

    chapitre = next(
        (c for c in structure.chapitres if c.page_debut <= page <= c.page_fin), None
    )
    if chapitre is None:
        premier = structure.chapitres[0].page_debut if structure.chapitres else 1
        nom = CHAPITRE_LIMINAIRE if page < premier else CHAPITRE_HORS_SOMMAIRE
        return {**vide, "chapitre": nom}

    candidates = [s for s in chapitre.sections if s.page_debut <= page]
    if not candidates:  # pages d'ouverture, avant la première section du chapitre
        return {**vide, "chapitre": chapitre.titre, "section_titre": SECTION_OUVERTURE}
    section = candidates[-1]
    return {"chapitre": chapitre.titre, "section_num": section.num, "section_titre": section.titre}


def est_dans_image(item, doc: DoclingDocument) -> bool:
    """Indique si un élément est rattaché (directement ou non) à une image Docling.

    Docling classe parfois les encadrés stylisés comme des images : leur texte est
    alors rattaché comme enfant de l'image. On remonte toute la chaîne des parents
    (un élément de liste a pour parent un groupe, lui-même enfant de l'image).
    """
    parent = item.parent
    while parent is not None:
        if parent.cref.startswith("#/pictures"):
            return True
        parent = parent.resolve(doc).parent
    return False


# --------------------------------------------------------------------------- #
# 3. Parcours du document
# --------------------------------------------------------------------------- #
def extraire_elements(
    doc: DoclingDocument, structure: StructureLivre
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attribue à chaque élément de contenu son adresse dans le livre.

    Chapitre et section viennent de la page ; sous-section et sous-partie viennent
    d'un état courant, mis à jour par les titres rencontrés et réinitialisé à
    chaque changement de section. Le texte des encadrés classés comme images est
    inclus (``traverse_pictures=True``) et marqué ``dans_image``.

    Args:
        doc: document Docling.
        structure: structure du livre.

    Returns:
        (éléments de contenu, repères structurels pour les diagnostics).
    """
    referentiel = ReferentielTitres.depuis_structure(structure)
    lignes, reperes = [], []
    etat = {"code": None, "sous_section": None, "sous_partie": None}
    section_courante = None

    for ordre, (item, _) in enumerate(doc.iterate_items(traverse_pictures=True)):
        if not isinstance(item, (TextItem, TableItem)):
            continue  # images, groupes

        page = item.prov[0].page_no if item.prov else None
        loc = localiser(page, structure)

        # Changement de section (déduit de la page) -> état vierge
        cle_section = (loc["chapitre"], loc["section_titre"])
        if cle_section != section_courante:
            section_courante = cle_section
            etat = dict.fromkeys(etat)

        type_contenu = "table" if isinstance(item, TableItem) else item.label.value

        if isinstance(item, TextItem) and item.label in LABELS_TITRE:
            a = analyser_titre(item.text, loc["section_num"], referentiel)
            if a["type"] != "pseudo_titre":
                reperes.append({"ordre": ordre, "page": page, **a,
                                "section_num_page": loc["section_num"], "origine": "titre"})
            if a["type"] == "sous_section":
                etat = {"code": a["code"], "sous_section": a["titre"], "sous_partie": None}
                continue
            if a["type"] in ("sous_partie", "renvoi_suspect"):
                etat["sous_partie"] = a["titre"]
                continue
            if a["type"] in ("titre_chapitre", "titre_section"):
                continue
            type_contenu = "pseudo_titre"  # conservé comme contenu ordinaire

        texte = item.export_to_markdown(doc=doc) if isinstance(item, TableItem) else item.text
        lignes.append({
            "ordre": ordre, **loc, **etat, "page": page,
            "type_contenu": type_contenu,
            "dans_image": est_dans_image(item, doc),
            "texte": texte,
        })

    df_elements = pd.DataFrame(lignes, columns=COLONNES_ELEMENTS)
    df_reperes = pd.DataFrame(reperes, columns=COLONNES_REPERES)
    logger.info("%d éléments de contenu, %d repères structurels", len(df_elements), len(df_reperes))
    return df_elements, df_reperes


# --------------------------------------------------------------------------- #
# 4. Diagnostics
# --------------------------------------------------------------------------- #
def diagnostiquer_structure(df_reperes: pd.DataFrame) -> dict:
    """Contrôles de cohérence des sous-sections détectées.

    Args:
        df_reperes: repères renvoyés par ``extraire_elements``.

    Returns:
        dict avec ``incoherents`` (codes dont la section ne correspond pas à la page),
        ``manquants`` (section -> numéros de sous-sections absents) et ``doublons``
        (code -> nombre d'occurrences).
    """
    codes = df_reperes[df_reperes["type"] == "sous_section"]
    incoherents = codes[codes["code"].str[:2] != codes["section_num_page"]]

    manquants = {}
    for sec, groupe in codes.groupby(codes["code"].str[:2]):
        numeros = {int(c[3:]) for c in groupe["code"]}
        absents = sorted(set(range(1, max(numeros) + 1)) - numeros)
        if absents:
            manquants[sec] = absents

    comptes = codes["code"].value_counts()
    return {
        "incoherents": incoherents["code"].tolist(),
        "manquants": manquants,
        "doublons": comptes[comptes > 1].to_dict(),
    }
