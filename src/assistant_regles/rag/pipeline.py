"""Pipeline d'indexation : chunks.jsonl -> embeddings BGE-M3 -> PostgreSQL / pgvector.

Lancement : ``uv run indexer-regles`` (après ``uv run ingest-regles`` et
``docker compose up -d``).

Deux niveaux :

- :func:`indexer` orchestre les étapes à partir d'objets déjà construits
  (chunks, encodeur, connexion). Aucune I/O cachée : testable avec un faux
  encodeur et une base testcontainers ;
- :func:`main` construit ces objets (``.env``, configs, jsonl, modèle) et
  appelle :func:`indexer`.

Étapes de :func:`indexer` :

1. schéma appliqué (idempotent) et dimension de la colonne vérifiée,
   **avant** l'encodage : une incohérence échoue sans calcul inutile ;
2. encodage des textes ;
3. synchronisation (upsert + orphelins, une transaction par lot) ;
4. contrôles de chaque périmètre écrit : volume, dimensions, normes
   (bloquants), auto-récupération (avertissement).
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from assistant_regles.rag.store import (
    Perimetre,
    Statistiques,
    appliquer_schema,
    grouper_par_perimetre,
    statistiques,
    synchroniser,
    verifier_dimension,
    vers_ligne,
)

if TYPE_CHECKING:
    import psycopg

    from assistant_regles.ingest.chunk import Chunk
    from assistant_regles.rag.embeddings import Encodeur

logger = logging.getLogger(__name__)

TOLERANCE_NORME = 1e-3  # écart à 1 toléré sur la norme des vecteurs stockés


@dataclass(frozen=True)
class RapportIndexation:
    """Résultat d'une indexation."""

    nb_chunks: int
    identifiant_modele: str
    orphelins: dict[Perimetre, int]
    statistiques: dict[Perimetre, Statistiques]
    avertissements: list[str]
    duree_encodage: float
    duree_ecriture: float


def controler(stats: Statistiques, nb_attendu: int, dimension: int) -> list[str]:
    """Contrôle un périmètre après écriture.

    Args:
        stats: mesures du périmètre en base.
        nb_attendu: nombre de chunks du lot pour ce périmètre.
        dimension: dimension de l'encodeur.

    Returns:
        Avertissements non bloquants (liste vide si tout est normal).

    Raises:
        ValueError: volume, dimension ou normes incohérents.
    """
    erreurs = []
    if stats.nb_lignes != nb_attendu:
        erreurs.append(f"{stats.nb_lignes} ligne(s) en base pour {nb_attendu} chunk(s)")
    if stats.dimensions != [dimension]:
        erreurs.append(f"dimensions en base {stats.dimensions}, attendu [{dimension}]")
    if (
        stats.norme_min is None
        or abs(stats.norme_min - 1) > TOLERANCE_NORME
        or abs(stats.norme_max - 1) > TOLERANCE_NORME
    ):
        erreurs.append(f"normes hors de 1 ± {TOLERANCE_NORME} : [{stats.norme_min}, {stats.norme_max}]")
    if erreurs:
        raise ValueError("Contrôle de l'indexation en échec : " + " ; ".join(erreurs))

    avertissements = []
    if stats.auto_recuperation < stats.nb_lignes:
        avertissements.append(
            f"{stats.nb_lignes - stats.auto_recuperation} chunk(s) n'ont pas eux-mêmes pour "
            "plus proche voisin (textes identiques ou quasi identiques ?)"
        )
    return avertissements


def indexer(
    chunks: Sequence[Chunk], encodeur: Encodeur, conn: psycopg.Connection
) -> RapportIndexation:
    """Encode les chunks et aligne la base sur eux, périmètre par périmètre.

    Args:
        chunks: chunks issus de l'ingestion.
        encodeur: tout objet respectant le contrat :class:`Encodeur`.
        conn: connexion psycopg ouverte (autocommit recommandé).

    Returns:
        Rapport de l'indexation.

    Raises:
        ValueError: aucun chunk, dimension incohérente, sortie d'encodeur
            inattendue ou contrôle final en échec.
    """
    if not chunks:
        raise ValueError("Aucun chunk à indexer : l'ingestion a-t-elle tourné ?")

    appliquer_schema(conn)
    verifier_dimension(conn, encodeur.dimension)

    logger.info("Encodage de %d chunks avec %s", len(chunks), encodeur.identifiant)
    debut = time.perf_counter()
    vecteurs = encodeur.encoder([c.texte for c in chunks])
    duree_encodage = time.perf_counter() - debut
    if vecteurs.shape != (len(chunks), encodeur.dimension):
        raise ValueError(
            f"Sortie de l'encodeur de forme {vecteurs.shape}, "
            f"attendu {(len(chunks), encodeur.dimension)}"
        )

    lignes = [vers_ligne(c, v, encodeur.identifiant) for c, v in zip(chunks, vecteurs, strict=True)]
    debut = time.perf_counter()
    orphelins = synchroniser(conn, lignes)
    duree_ecriture = time.perf_counter() - debut

    stats, avertissements = {}, []
    for perimetre, ids in grouper_par_perimetre(lignes).items():
        stats[perimetre] = statistiques(conn, *perimetre)
        for message in controler(stats[perimetre], len(ids), encodeur.dimension):
            logger.warning("%s / %s : %s", *perimetre, message)
            avertissements.append(message)

    return RapportIndexation(
        nb_chunks=len(chunks),
        identifiant_modele=encodeur.identifiant,
        orphelins=orphelins,
        statistiques=stats,
        avertissements=avertissements,
        duree_encodage=duree_encodage,
        duree_ecriture=duree_ecriture,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de ``uv run indexer-regles``.

    Returns:
        Code de sortie : 0 si l'indexation a réussi, 1 sinon.
    """
    parser = argparse.ArgumentParser(description="Indexe chunks.jsonl dans PostgreSQL / pgvector.")
    parser.add_argument("-v", "--verbeux", action="store_true", help="journalisation DEBUG")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbeux else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # requêtes HTTP de huggingface_hub

    # Imports lourds ou liés à l'environnement : seulement à l'exécution
    import psycopg
    from dotenv import load_dotenv

    from assistant_regles.ingest.chunk import lire_jsonl
    from assistant_regles.ingest.config import charger_config as charger_config_ingest
    from assistant_regles.ingest.config import trouver_racine
    from assistant_regles.rag.config import charger_config as charger_config_rag
    from assistant_regles.rag.embeddings import EncodeurBGEM3

    load_dotenv(trouver_racine() / ".env")
    chemin_chunks = charger_config_ingest().chemin_chunks
    config_rag = charger_config_rag()

    if not chemin_chunks.exists():
        logger.error("%s introuvable : lancer d'abord `uv run ingest-regles`", chemin_chunks)
        return 1
    chunks = lire_jsonl(chemin_chunks)
    logger.info("%d chunks lus depuis %s", len(chunks), chemin_chunks)

    # Connexion AVANT le chargement du modèle : si la base est éteinte,
    # on échoue tout de suite au lieu d'attendre le modèle.
    try:
        conn = psycopg.connect(autocommit=True)
    except psycopg.OperationalError as e:
        logger.error("Base injoignable (`docker compose up -d` ?) : %s", e)
        return 1

    with conn:
        logger.info("Base : %s@%s:%s/%s", conn.info.user, conn.info.host, conn.info.port, conn.info.dbname)
        encodeur = EncodeurBGEM3.charger(config_rag.embeddings)
        try:
            rapport = indexer(chunks, encodeur, conn)
        except ValueError as e:
            logger.error("%s", e)
            return 1

    logger.info(
        "Indexation terminée : %d chunks, encodage %.1f s, écriture %.2f s",
        rapport.nb_chunks, rapport.duree_encodage, rapport.duree_ecriture,
    )
    for (source, edition), stats in rapport.statistiques.items():
        logger.info(
            "%s / %s : %d ligne(s), %d orpheline(s) supprimée(s), auto-récupération %d/%d",
            source, edition, stats.nb_lignes, rapport.orphelins[(source, edition)],
            stats.auto_recuperation, stats.nb_lignes,
        )
    return 0
