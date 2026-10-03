"""Historique de conversation transmis à l'agent et au générateur.

Ce qui est transmis à chaque nouvelle question (voir aussi agent.py et generation.py) :

- les N derniers tours (``agent.historique_tours``) : question du joueur et texte de la
  réponse, tronqué à ``agent.historique_max_caracteres`` ;
- pour l'agent seulement : les codes des règles citées dans ces réponses, qu'il peut
  relire avec ``lire_regle`` ;
- **jamais** le texte des passages des tours précédents. Seuls les passages du tour
  courant sont des sources : l'ancrage et la numérotation des citations restent
  propres au tour.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistant_regles.rag.generation import Reponse

MARQUE_TRONCATURE = " […]"


@dataclass(frozen=True)
class TourConversation:
    """Un échange terminé : question du joueur, texte de la réponse, règles citées."""

    question: str
    reponse: str
    codes_cites: tuple[str, ...] = ()


def depuis_reponse(reponse: Reponse) -> TourConversation:
    """Tour de conversation à partir d'une réponse générée (codes cités sans doublon, dans l'ordre)."""
    codes = tuple(dict.fromkeys(c.code for c in reponse.citations if c.code))
    return TourConversation(reponse.question, reponse.texte, codes)


def fenetre(tours: Sequence[TourConversation], nb_tours: int) -> list[TourConversation]:
    """Les ``nb_tours`` derniers tours (aucun si ``nb_tours`` vaut 0)."""
    return list(tours[-nb_tours:]) if nb_tours > 0 else []


def tronquer(texte: str, max_caracteres: int) -> str:
    """Texte coupé à ``max_caracteres`` (marque de troncature comprise dans la limite)."""
    if len(texte) <= max_caracteres:
        return texte
    return texte[: max(0, max_caracteres - len(MARQUE_TRONCATURE))].rstrip() + MARQUE_TRONCATURE


def texte_pour_agent(tours: Sequence[TourConversation], max_caracteres: int) -> str:
    """Historique balisé pour l'agent : échanges, puis codes des règles déjà citées.

    Format (décrit dans le prompt de l'agent) ::

        <historique_conversation>
        <echange>
        <question>…</question>
        <reponse>…</reponse>
        </echange>
        <regles_citees>10.04, 09.07</regles_citees>
        </historique_conversation>

    Le texte est échappé (« < », « > », « & ») : une réponse ne peut pas fermer une
    balise par erreur.
    """
    lignes = ["<historique_conversation>"]
    for tour in tours:
        lignes += [
            "<echange>",
            f"<question>{html.escape(tour.question, quote=False)}</question>",
            f"<reponse>{html.escape(tronquer(tour.reponse, max_caracteres), quote=False)}</reponse>",
            "</echange>",
        ]
    codes = list(dict.fromkeys(c for tour in tours for c in tour.codes_cites))
    if codes:
        lignes.append(f"<regles_citees>{', '.join(codes)}</regles_citees>")
    lignes.append("</historique_conversation>")
    return "\n".join(lignes)
