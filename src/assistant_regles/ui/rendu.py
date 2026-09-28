"""Mise en forme des réponses pour l'interface (fonctions pures, sans Streamlit)."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from assistant_regles.ui.style import MARQUEUR_UTILISATEUR

if TYPE_CHECKING:
    from assistant_regles.rag.agent import Selection
    from assistant_regles.rag.config import ConfigRag
    from assistant_regles.rag.generation import PromptSysteme, Reponse


def echapper_markdown(texte: str) -> str:
    """Neutralise « $ », que Streamlit interpréterait comme du LaTeX."""
    return texte.replace("$", r"\$")


def numeros_citations(reponse: Reponse) -> dict[tuple[int, int, int], int]:
    """Numéro de note de chaque citation distincte, dans l'ordre d'apparition."""
    return {(c.rang_resultat, c.bloc_debut, c.bloc_fin): i
            for i, c in enumerate(reponse.citations, start=1)}


def markdown_reponse(reponse: Reponse) -> str:
    """Texte de la réponse avec ses notes en exposant (<sup>[1]</sup>)."""
    numeros = numeros_citations(reponse)
    morceaux = []
    for segment in reponse.segments:
        morceaux.append(echapper_markdown(segment.texte))
        notes = sorted({numeros[(c.rang_resultat, c.bloc_debut, c.bloc_fin)] for c in segment.citations})
        if notes:
            morceaux.append("<sup>" + "".join(f"[{n}]" for n in notes) + "</sup>")
    return "".join(morceaux).strip()


def markdown_sources(reponse: Reponse, largeur_extrait: int = 400) -> str:
    """Liste des sources : titre du passage puis extrait cité, en citation."""
    blocs = []
    for numero, c in enumerate(reponse.citations, start=1):
        extrait = " ".join(c.texte_cite.split())
        if len(extrait) > largeur_extrait:
            extrait = extrait[:largeur_extrait].rstrip() + " …"
        blocs.append(f"**[{numero}] {echapper_markdown(c.titre)}**\n\n> {echapper_markdown(extrait)}")
    return '<div class="sources-citees">\n\n' + "\n\n".join(blocs) + "\n\n</div>"


def legende_technique(reponse: Reponse) -> str:
    """Ligne discrète : modèle, prompt, durée, tokens, part citée."""
    return (
        f"{reponse.modele} · prompt {reponse.prompt} [{reponse.empreinte_prompt[:8]}] · "
        f"{reponse.duree:.1f} s · {reponse.tokens_entree} → {reponse.tokens_sortie} tokens · "
        f"{reponse.part_citee:.0%} du texte cité"
    )


def legende_agent(selection: Selection) -> str:
    """Ligne discrète sur la recherche de l'agent."""
    repli = f" · fin par repli ({selection.fin})" if selection.fin != "retenue" else ""
    return (
        f"agent {selection.modele} · {selection.tours} tour(s) · {selection.nb_appels_outils} recherche(s) · "
        f"{len(selection.passages)}/{selection.vus} passages retenus · "
        f"{selection.tokens_entree} → {selection.tokens_sortie} tokens · {selection.duree:.1f} s{repli}"
    )


def markdown_question(question: str) -> str:
    """Question de l'utilisateur, marquée pour le style « bulle », HTML échappé."""
    return MARQUEUR_UTILISATEUR + echapper_markdown(html.escape(question))


def tableau_configuration(
    config: ConfigRag, prompt: PromptSysteme | None, prompt_agent: PromptSysteme | None = None
) -> list[tuple[str, str]]:
    """Paramètres affichés dans la page « Informations sur le modèle »."""
    e, r, g, a = config.embeddings, config.recherche, config.generation, config.agent
    agent = [
        ("Agent de recherche", f"{'activé' if a.actif else 'désactivé'} par défaut"),
        ("Modèle de l'agent", f"{a.modele} (effort {a.effort or 'par défaut'})"),
        ("Budget de l'agent", f"{a.max_tours} tours, {a.max_passages} passages retenus au plus, "
                              f"{a.k} passages par recherche, amorce {'activée' if a.amorce else 'désactivée'}"),
        ("Prompt de l'agent", f"{a.prompt} [{prompt_agent.empreinte[:8]}]" if prompt_agent else a.prompt),
    ]
    return [
        ("Modèle d'embedding", f"{e.modele} (révision {e.revision[:8]}, dimension {e.dimension})"),
        ("Passages transmis par question sans agent (k)", str(r.k)),
        ("Modèle de génération", g.modele),
        ("Effort", g.effort),
        ("Réflexion", "adaptative" if g.reflexion else "désactivée"),
        ("Granularité des citations", g.granularite),
        ("Prompt système", f"{g.prompt_systeme} [{prompt.empreinte[:8]}]" if prompt else g.prompt_systeme),
        *agent,
    ]
