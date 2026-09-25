"""Recherche dense : question -> top-k chunks les plus proches (similarité cosinus).

Lancement manuel : ``uv run chercher-regles "ma question" [-k 5] [--complet]``.

:class:`MoteurRecherche` vérifie **une fois**, à sa construction, que la base
est utilisable (table présente, non vide, indexée avec le même modèle que
l'encodeur), puis répond à autant de questions que nécessaire. Le garde-fou ne
peut donc pas être contourné, et l'évaluation (des dizaines de questions) ne le
paie qu'une fois.

Score renvoyé : ``1 - distance cosinus`` = similarité cosinus. Plus il est haut,
plus le chunk est proche. Seul l'ordre compte : les valeurs absolues sont
élevées même pour des textes sans rapport (anisotropie, notebooks 03 et 05).
"""

from __future__ import annotations

import argparse
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pgvector.psycopg import register_vector

from assistant_regles.rag.store import TABLE

if TYPE_CHECKING:
    import psycopg

    from assistant_regles.rag.embeddings import Encodeur

logger = logging.getLogger(__name__)

COLONNES_RESULTAT = (
    "id", "code", "chapitre", "section_num", "section_titre", "sous_section",
    "page_debut", "page_fin", "type_contenu", "source", "edition", "codes_cites", "texte",
)

SQL_RECHERCHE = f"""SELECT {", ".join(COLONNES_RESULTAT)},
       1 - (embedding <=> %(q)s) AS score
FROM {TABLE}
ORDER BY embedding <=> %(q)s
LIMIT %(k)s"""


@dataclass(frozen=True)
class Resultat:
    """Un chunk retrouvé, avec ce qu'il faut pour le citer et l'évaluer."""

    rang: int
    score: float
    id: uuid.UUID
    code: str | None
    chapitre: str
    section_num: str | None
    section_titre: str | None
    sous_section: str | None
    page_debut: int
    page_fin: int
    type_contenu: str
    source: str
    edition: str
    codes_cites: list[str]
    texte: str


def valider_requete(question: str, k: int) -> None:
    """Refuse une question vide ou un k non positif.

    Raises:
        ValueError: question vide (ou blanche) ou k < 1.
    """
    if not question.strip():
        raise ValueError("Question vide")
    if k < 1:
        raise ValueError(f"k doit être au moins 1 (reçu {k})")


def verifier_base(conn: psycopg.Connection, identifiant_modele: str) -> None:
    """Vérifie que la table existe, n'est pas vide et a été indexée avec ce modèle.

    Raises:
        RuntimeError: table absente ou vide (indexation à lancer).
        ValueError: vecteurs produits par un autre modèle (réindexation à lancer).
    """
    if conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] is None:
        raise RuntimeError(f"Table {TABLE} absente : lancer `uv run indexer-regles`")
    modeles = [r[0] for r in conn.execute(f"SELECT DISTINCT modele_embedding FROM {TABLE}")]
    if not modeles:
        raise RuntimeError(f"Table {TABLE} vide : lancer `uv run indexer-regles`")
    if modeles != [identifiant_modele]:
        raise ValueError(
            f"Base indexée avec {modeles}, encodeur courant {identifiant_modele} : "
            "vecteurs non comparables, relancer `uv run indexer-regles`"
        )


class MoteurRecherche:
    """Recherche top-k sur la base, avec un encodeur vérifié compatible."""

    def __init__(self, encodeur: Encodeur, conn: psycopg.Connection) -> None:
        """Vérifie la base puis prépare la connexion.

        Raises:
            RuntimeError: table absente ou vide.
            ValueError: base indexée avec un autre modèle.
        """
        verifier_base(conn, encodeur.identifiant)
        register_vector(conn)  # après la vérification : l'extension existe forcément
        self._encodeur = encodeur
        self._conn = conn

    def rechercher(self, question: str, k: int) -> list[Resultat]:
        """Renvoie les k chunks les plus proches de la question, du plus proche au moins proche.

        Raises:
            ValueError: question vide, k < 1, ou question trop longue pour l'encodeur.
        """
        from psycopg.rows import dict_row

        valider_requete(question, k)
        vecteur = self._encodeur.encoder([question])[0]
        with self._conn.cursor(row_factory=dict_row) as cur:
            lignes = cur.execute(SQL_RECHERCHE, {"q": vecteur, "k": k}).fetchall()
        return [Resultat(rang=i, **ligne) for i, ligne in enumerate(lignes, start=1)]


# --------------------------------------------------------------------------- #
# Affichage et CLI
# --------------------------------------------------------------------------- #
def formater_resultat(resultat: Resultat, texte_complet: bool = False, largeur: int = 200) -> str:
    """Met en forme un résultat : ligne d'en-tête, puis texte complet ou aperçu."""
    r = resultat
    pages = f"p. {r.page_debut}" + (f"-{r.page_fin}" if r.page_fin != r.page_debut else "")
    section = f"{r.section_num or '--'} {r.section_titre or ''}".strip()
    entete = (
        f"#{r.rang}  {r.score:.3f}  {r.code or '(sans code)'}  |  "
        f"{section} > {r.sous_section or '-'}  |  {pages}  |  {r.type_contenu}"
    )
    if texte_complet:
        return f"{entete}\n{r.texte}\n"
    apercu = " ".join(r.texte.split())
    if len(apercu) > largeur:
        apercu = apercu[:largeur].rstrip() + " …"
    return f"{entete}\n    {apercu}\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de ``uv run chercher-regles``.

    Les résultats sont le produit de la commande : ils vont sur la sortie
    standard (``print``), ce qui permet de les rediriger. Les messages de
    diagnostic passent par ``logging`` (sortie d'erreur), en WARNING par défaut
    pour ne pas noyer les résultats ; ``-v`` affiche le détail.

    Returns:
        Code de sortie : 0 si la recherche a abouti, 1 sinon.
    """
    parser = argparse.ArgumentParser(description="Recherche les chunks les plus proches d'une question.")
    parser.add_argument("question", help="question en langage naturel (entre guillemets)")
    parser.add_argument("-k", type=int, default=None, help="nombre de résultats (défaut : config)")
    parser.add_argument("--complet", action="store_true", help="afficher le texte complet des chunks")
    parser.add_argument("-v", "--verbeux", action="store_true", help="journalisation INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbeux else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # requêtes HTTP de huggingface_hub

    import psycopg
    from dotenv import load_dotenv

    from assistant_regles.ingest.config import trouver_racine
    from assistant_regles.rag.config import charger_config
    from assistant_regles.rag.embeddings import EncodeurBGEM3, identifiant_modele

    load_dotenv(trouver_racine() / ".env")
    config = charger_config()
    k = args.k if args.k is not None else config.recherche.k
    try:
        valider_requete(args.question, k)
    except ValueError as e:
        logger.error("%s", e)
        return 1

    try:
        conn = psycopg.connect(autocommit=True)
    except psycopg.OperationalError as e:
        logger.error("Base injoignable (`docker compose up -d` ?) : %s", e)
        return 1

    with conn:
        try:
            # Vérification AVANT de charger le modèle : échec immédiat si la base
            # est vide ou indexée avec un autre modèle (MoteurRecherche revérifie).
            verifier_base(conn, identifiant_modele(config.embeddings))
            moteur = MoteurRecherche(EncodeurBGEM3.charger(config.embeddings), conn)
            resultats = moteur.rechercher(args.question, k)
        except (RuntimeError, ValueError) as e:
            logger.error("%s", e)
            return 1

    print(f"Question : {args.question}\n")
    for resultat in resultats:
        print(formater_resultat(resultat, texte_complet=args.complet))
    return 0
