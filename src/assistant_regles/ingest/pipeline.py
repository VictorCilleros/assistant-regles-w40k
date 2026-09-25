"""Pipeline d'ingestion de bout en bout : PDF -> éléments structurés -> chunks JSONL.

Usage :
    uv run ingest-regles                      # utilise le cache Docling s'il existe
    uv run ingest-regles --forcer-conversion  # reconvertit le PDF
    uv run ingest-regles -v                   # journalisation détaillée

(équivalent : uv run python -m assistant_regles.ingest.pipeline)
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from assistant_regles.ingest.chunk import (
    CompteurTokens,
    charger_compteur_tokens,
    construire_chunks,
    ecrire_jsonl,
)
from assistant_regles.ingest.clean import (
    annoter_nettoyage,
    contenu_conserve,
    rapport_nettoyage,
)
from assistant_regles.ingest.config import ConfigIngestion, charger_config
from assistant_regles.ingest.parse import (
    convertir_pdf,
    diagnostiquer_structure,
    extraire_elements,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultatIngestion:
    """Volumes et artefacts produits par une exécution du pipeline."""

    nb_elements: int
    nb_elements_conserves: int
    nb_chunks: int
    chemin_elements: Path
    chemin_chunks: Path


def executer(
    config: ConfigIngestion,
    forcer_conversion: bool = False,
    compter_tokens: CompteurTokens | None = None,
) -> ResultatIngestion:
    """Exécute l'ingestion complète et écrit les artefacts dans data/interim.

    Args:
        config: configuration validée.
        forcer_conversion: reconvertit le PDF même si le cache Docling existe.
        compter_tokens: compteur de tokens (défaut : tokenizer de la config).

    Returns:
        Volumes et chemins des artefacts.
    """
    params = config.pipeline

    # 1. Conversion + structure
    doc = convertir_pdf(
        config.chemin_pdf,
        config.chemin_cache_docling,
        ocr=params.docling.ocr,
        structure_tableaux=params.docling.structure_tableaux,
        forcer=forcer_conversion,
    )
    df_elements, df_reperes = extraire_elements(doc, config.structure)

    diagnostic = diagnostiquer_structure(df_reperes)
    if diagnostic["incoherents"]:
        logger.warning("Codes incohérents avec leur page : %s", diagnostic["incoherents"])
    if diagnostic["manquants"]:
        logger.warning("Sous-sections absentes : %s", diagnostic["manquants"])
    if diagnostic["doublons"]:
        logger.info("Codes présents plusieurs fois : %s", diagnostic["doublons"])

    # 2. Nettoyage (annotation) + sauvegarde pour audit
    df_elements = annoter_nettoyage(df_elements, params.nettoyage)
    logger.info("Nettoyage :\n%s", rapport_nettoyage(df_elements).to_string())
    config.chemin_elements.parent.mkdir(parents=True, exist_ok=True)
    df_elements.to_parquet(config.chemin_elements, index=False)

    # 3. Chunking
    df_contenu = contenu_conserve(df_elements)
    compter = compter_tokens or charger_compteur_tokens(params.chunking.tokenizer)
    chunks = construire_chunks(df_contenu, params.chunking, params.document, compter)
    ecrire_jsonl(chunks, config.chemin_chunks)
    logger.info("Chunks écrits dans %s", config.chemin_chunks)

    return ResultatIngestion(
        nb_elements=len(df_elements),
        nb_elements_conserves=len(df_contenu),
        nb_chunks=len(chunks),
        chemin_elements=config.chemin_elements,
        chemin_chunks=config.chemin_chunks,
    )


def main(argv: list[str] | None = None) -> None:
    """Point d'entrée en ligne de commande."""
    parser = argparse.ArgumentParser(description="Ingestion du livre de règles en chunks.")
    parser.add_argument("--config", type=Path, default=None,
                        help="Dossier de configuration (défaut : <racine>/config/ingest)")
    parser.add_argument("--forcer-conversion", action="store_true",
                        help="Reconvertit le PDF même si le cache Docling existe")
    parser.add_argument("-v", "--verbose", action="store_true", help="Journalisation détaillée")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    resultat = executer(charger_config(args.config), forcer_conversion=args.forcer_conversion)
    logger.info(
        "Terminé : %d éléments, %d conservés, %d chunks",
        resultat.nb_elements, resultat.nb_elements_conserves, resultat.nb_chunks,
    )


if __name__ == "__main__":
    main()
