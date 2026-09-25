"""Écriture des chunks et de leurs embeddings dans PostgreSQL / pgvector.

Toutes les fonctions reçoivent une connexion psycopg déjà ouverte (injection) :
la CLI la crée à partir du .env, les tests à partir d'un conteneur testcontainers.

Synchronisation d'un lot de chunks (:func:`synchroniser`), en une seule transaction :

1. upsert sur l'id déterministe (``INSERT … ON CONFLICT (id) DO UPDATE``) ;
2. suppression des orphelins : lignes du même périmètre (source, édition)
   absentes du lot, par exemple après un changement de découpage.

Le nom de table et les colonnes sont des constantes du code, pas des paramètres :
ils doivent correspondre à ``sql/schema.sql`` (vérifié par les tests). Les requêtes
sont assemblées à partir de ces seules constantes, jamais d'une entrée extérieure.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

import numpy as np
from pgvector.psycopg import register_vector

if TYPE_CHECKING:
    import psycopg

    from assistant_regles.ingest.chunk import Chunk

logger = logging.getLogger(__name__)

TABLE = "chunks_w40k"

COLONNES = (
    "id", "texte", "chapitre", "section_num", "section_titre", "code", "sous_section",
    "sous_parties", "page_debut", "page_fin", "type_contenu", "edition", "source",
    "codes_cites", "partie", "nb_parties", "nb_tokens", "hors_budget", "ordres",
    "embedding", "modele_embedding", "empreinte_texte",
)

type Ligne = dict[str, Any]
type Perimetre = tuple[str, str]  # (source, édition)

_MISES_A_JOUR = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in COLONNES if c != "id")

SQL_UPSERT = f"""INSERT INTO {TABLE} ({", ".join(COLONNES)})
VALUES ({", ".join(f"%({c})s" for c in COLONNES)})
ON CONFLICT (id) DO UPDATE SET
    {_MISES_A_JOUR},
    indexe_le = now()"""

SQL_ORPHELINS = f"""DELETE FROM {TABLE}
WHERE source = %s AND edition = %s AND NOT (id = ANY(%s))"""

# Pour une colonne vector(N), pgvector stocke N dans atttypmod
SQL_DIMENSION = """SELECT atttypmod FROM pg_attribute
WHERE attrelid = %s::regclass AND attname = 'embedding'"""


# --------------------------------------------------------------------------- #
# Schéma
# --------------------------------------------------------------------------- #
def lire_schema() -> str:
    """Contenu de ``sql/schema.sql``, lu depuis le package."""
    return resources.files("assistant_regles.rag").joinpath("sql/schema.sql").read_text(encoding="utf-8")


def appliquer_schema(conn: psycopg.Connection) -> None:
    """Applique le schéma (idempotent) puis enregistre le type ``vector``.

    L'ordre est imposé : ``register_vector`` interroge la base sur le type
    ``vector``, qui n'existe qu'une fois l'extension créée.
    """
    with conn.transaction():
        conn.execute(lire_schema())
    register_vector(conn)
    logger.info("Schéma appliqué (table %s)", TABLE)


def verifier_dimension(conn: psycopg.Connection, dimension: int) -> None:
    """Vérifie que la colonne ``embedding`` a la dimension attendue.

    Raises:
        ValueError: la colonne n'a pas la dimension de l'encodeur.
    """
    dimension_colonne = conn.execute(SQL_DIMENSION, (TABLE,)).fetchone()[0]
    if dimension_colonne != dimension:
        raise ValueError(
            f"Colonne {TABLE}.embedding de dimension {dimension_colonne}, "
            f"encodeur de dimension {dimension}"
        )


# --------------------------------------------------------------------------- #
# Chunk -> ligne SQL
# --------------------------------------------------------------------------- #
def empreinte(texte: str) -> str:
    """Hash sha256 (hexadécimal) du texte embarqué."""
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def vers_ligne(chunk: Chunk, vecteur: np.ndarray, identifiant_modele: str) -> Ligne:
    """Convertit un chunk et son vecteur en ligne SQL (clés = :data:`COLONNES`).

    L'id devient un ``uuid.UUID`` : transmis comme texte, il ne pourrait pas être
    comparé à la colonne ``uuid`` dans ``id = ANY(...)``.
    """
    return {
        "id": uuid.UUID(str(chunk.id)),
        "texte": chunk.texte,
        "chapitre": chunk.chapitre,
        "section_num": chunk.section_num,
        "section_titre": chunk.section_titre,
        "code": chunk.code,
        "sous_section": chunk.sous_section,
        "sous_parties": list(chunk.sous_parties),
        "page_debut": chunk.page_debut,
        "page_fin": chunk.page_fin,
        "type_contenu": chunk.type_contenu,
        "edition": chunk.edition,
        "source": chunk.source,
        "codes_cites": list(chunk.codes_cites),
        "partie": chunk.partie,
        "nb_parties": chunk.nb_parties,
        "nb_tokens": chunk.nb_tokens,
        "hors_budget": chunk.hors_budget,
        "ordres": list(chunk.ordres),
        "embedding": np.asarray(vecteur, dtype=np.float32),
        "modele_embedding": identifiant_modele,
        "empreinte_texte": empreinte(chunk.texte),
    }


def grouper_par_perimetre(lignes: Sequence[Ligne]) -> dict[Perimetre, list[uuid.UUID]]:
    """Regroupe les ids des lignes par (source, édition)."""
    perimetres: dict[Perimetre, list[uuid.UUID]] = {}
    for ligne in lignes:
        perimetres.setdefault((ligne["source"], ligne["edition"]), []).append(ligne["id"])
    return perimetres


# --------------------------------------------------------------------------- #
# Écriture
# --------------------------------------------------------------------------- #
def supprimer_orphelins(
    conn: psycopg.Connection, source: str, edition: str, ids: Sequence[uuid.UUID]
) -> int:
    """Supprime les lignes du périmètre dont l'id n'est pas dans ``ids``.

    Returns:
        Nombre de lignes supprimées.

    Raises:
        ValueError: ``ids`` est vide. ``NOT (id = ANY('{}'))`` étant vrai pour
            toutes les lignes, la requête viderait tout le périmètre.
    """
    if not ids:
        raise ValueError(
            f"Liste d'ids vide pour ({source}, {edition}) : refus de vider tout le périmètre"
        )
    return conn.execute(SQL_ORPHELINS, (source, edition, list(ids))).rowcount


def synchroniser(conn: psycopg.Connection, lignes: Sequence[Ligne]) -> dict[Perimetre, int]:
    """Aligne la table sur ``lignes`` pour chacun de leurs périmètres, atomiquement.

    Upsert de toutes les lignes, puis suppression des orphelins de chaque
    périmètre présent dans le lot. Les autres périmètres ne sont pas touchés.
    En cas d'erreur, rien n'est écrit.

    Returns:
        Nombre d'orphelins supprimés par périmètre.
    """
    if not lignes:
        logger.warning("Aucune ligne à synchroniser : rien n'est modifié")
        return {}

    perimetres = grouper_par_perimetre(lignes)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(SQL_UPSERT, lignes)
        supprimes = {p: supprimer_orphelins(conn, *p, ids) for p, ids in perimetres.items()}

    for (source, edition), nb in supprimes.items():
        logger.info(
            "%s / %s : %d ligne(s) écrite(s), %d orpheline(s) supprimée(s)",
            source, edition, len(perimetres[(source, edition)]), nb,
        )
    return supprimes


# --------------------------------------------------------------------------- #
# Contrôles
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Statistiques:
    """État d'un périmètre dans la base."""

    nb_lignes: int
    dimensions: list[int]
    norme_min: float | None
    norme_max: float | None
    auto_recuperation: int  # lignes dont le plus proche voisin est elle-même


def statistiques(conn: psycopg.Connection, source: str, edition: str) -> Statistiques:
    """Mesure un périmètre : volume, dimensions, normes et auto-récupération.

    L'auto-récupération compare chaque ligne à toutes les autres du périmètre :
    adaptée à quelques centaines de chunks, pas à des millions.
    """
    nb, dimensions, norme_min, norme_max = conn.execute(
        f"""SELECT count(*),
                   coalesce(array_agg(DISTINCT vector_dims(embedding)), '{{}}'),
                   min(vector_norm(embedding)),
                   max(vector_norm(embedding))
            FROM {TABLE} WHERE source = %s AND edition = %s""",
        (source, edition),
    ).fetchone()
    (auto,) = conn.execute(
        f"""SELECT count(*) FILTER (WHERE voisin.id = c.id)
            FROM {TABLE} c
            CROSS JOIN LATERAL (
                SELECT o.id FROM {TABLE} o
                WHERE o.source = c.source AND o.edition = c.edition
                ORDER BY o.embedding <=> c.embedding
                LIMIT 1
            ) AS voisin
            WHERE c.source = %s AND c.edition = %s""",
        (source, edition),
    ).fetchone()
    return Statistiques(nb, list(dimensions), norme_min, norme_max, auto)
