"""Agent de recherche : choisit les passages du livre avant la génération.

Architecture en deux étapes (notebook 07) :

1. l'**agent** reçoit la question, reformule avec le vocabulaire du livre, lance
   plusieurs recherches, suit les renvois entre règles, puis **sélectionne** les
   passages utiles avec l'outil ``retenir_passages`` ;
2. le **générateur** (:mod:`generation`, inchangé) rédige la réponse à partir de
   cette sélection seulement.

L'agent ne dépend que du contrat :class:`SourceRegles` (``rechercher`` et
``lire_regle``) : la recherche dense actuelle comme la future recherche hybride
avec reranking peuvent le remplir sans toucher à ce module.

Fins possibles de la recherche (:attr:`Selection.fin`) :

- ``retenue`` : l'agent a appelé ``retenir_passages`` (liste vide = rien ne
  concerne la question : l'appelant répond par une abstention directe) ;
- ``repli_etiquettes`` : étiquettes toutes inconnues, donc une erreur de l'agent
  et non une décision d'abstention ;
- ``repli_texte`` : l'agent a répondu en texte au lieu de sélectionner ;
- ``repli_budget`` : budget de tours épuisé.

Dans les trois cas de repli, les passages vus sont retenus dans l'ordre, dans la
limite de ``max_passages``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from assistant_regles.rag.generation import PromptSysteme, empreinte_texte, lire_prompt, titre_resultat

if TYPE_CHECKING:
    from assistant_regles.rag.config import ParamsAgent
    from assistant_regles.rag.recherche import Resultat

logger = logging.getLogger(__name__)

OUTIL_RECHERCHE = "rechercher_regles"
OUTIL_LECTURE = "lire_regle"
OUTIL_FIN = "retenir_passages"
RAPPEL_BUDGET = "Budget de recherche épuisé : appelle maintenant retenir_passages avec les passages utiles."

type Fin = Literal["retenue", "repli_etiquettes", "repli_texte", "repli_budget"]


class SourceRegles(Protocol):
    """Ce dont l'agent a besoin pour interroger le livre."""

    def rechercher(self, question: str, k: int) -> list[Resultat]: ...

    def lire_regle(self, code: str) -> list[Resultat]: ...


# --------------------------------------------------------------------------- #
# Prompt et outils
# --------------------------------------------------------------------------- #
def charger_prompt_agent(params: ParamsAgent) -> PromptSysteme:
    """Lit le prompt de l'agent et y reporte son budget.

    Marqueurs remplacés dans le fichier : ``{max_passages}`` et
    ``{max_tours_recherche}`` (tours disponibles pour chercher, le dernier
    servant à conclure). L'empreinte porte sur le texte final.
    """
    brut = lire_prompt(params.prompt)
    texte = brut.texte.replace("{max_passages}", str(params.max_passages)).replace(
        "{max_tours_recherche}", str(params.max_tours - 1)
    )
    return PromptSysteme(brut.nom, texte, empreinte_texte(texte))


def definir_outils(max_passages: int) -> list[dict[str, Any]]:
    """Les trois outils de l'agent. Leur description fait partie du prompt."""
    return [
        {
            "name": OUTIL_RECHERCHE,
            "description": (
                "Recherche dans le livre de règles de base de Warhammer 40,000 (11e édition) les passages les plus "
                "proches, par le sens, de la requête. Écris la requête avec le vocabulaire probable du livre (noms "
                "de phases, d'actions, mots-clés en majuscules) plutôt qu'avec celui du joueur. Une requête par "
                "règle : pour une question qui touche plusieurs règles, fais plusieurs appels. Chaque passage "
                "renvoyé porte une étiquette (P1, P2…), son code de règle, ses pages et ses renvois."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "requete": {"type": "string", "description": "Requête formulée avec le vocabulaire du livre."},
                    "k": {"type": "integer", "minimum": 1, "maximum": 10,
                          "description": "Nombre de passages voulus (valeur par défaut de la configuration)."},
                },
                "required": ["requete"],
            },
        },
        {
            "name": OUTIL_LECTURE,
            "description": (
                "Renvoie le texte complet d'une règle à partir de son code « XX.YY ». À utiliser pour suivre un "
                "renvoi indiqué dans un passage, ou lire une règle dont on connaît le code."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": r"^\d{2}\.\d{2}$",
                                        "description": "Code de la règle, par exemple 03.01."}},
                "required": ["code"],
            },
        },
        {
            "name": OUTIL_FIN,
            "description": (
                "Termine la recherche. Indique les étiquettes des passages nécessaires pour répondre complètement "
                "à la question, du plus important au moins important. Seuls ces passages seront transmis au "
                "rédacteur de la réponse. Liste vide si aucun passage du livre ne concerne la question."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "etiquettes": {"type": "array", "items": {"type": "string", "pattern": r"^P\d+$"},
                                   "maxItems": max_passages},
                    "justification": {"type": "string",
                                      "description": "En une ou deux phrases : pourquoi ces passages suffisent."},
                },
                "required": ["etiquettes", "justification"],
            },
        },
    ]


# --------------------------------------------------------------------------- #
# Trace et résultat
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AppelOutil:
    """Une étape de la recherche, telle qu'on l'affiche (en direct ou dans la trace)."""

    outil: str  # amorce | rechercher_regles | lire_regle | retenir_passages
    argument: str
    codes: tuple[str | None, ...] = ()
    erreur: str | None = None


def decrire_appel(appel: AppelOutil) -> str:
    """Une ligne lisible pour une étape de la recherche."""
    libelles = {
        "amorce": "Recherche initiale",
        OUTIL_RECHERCHE: "Recherche",
        OUTIL_LECTURE: "Lecture de la règle",
        OUTIL_FIN: "Passages retenus",
    }
    codes = ", ".join(c or "(sans code)" for c in appel.codes) or "aucun"
    suite = f"erreur : {appel.erreur}" if appel.erreur else codes
    return f"{libelles.get(appel.outil, appel.outil)} « {appel.argument} » → {suite}"


@dataclass(frozen=True)
class Selection:
    """Résultat de l'agent : passages retenus et déroulé de la recherche."""

    passages: tuple[Resultat, ...]
    justification: str
    fin: Fin
    appels: tuple[AppelOutil, ...]
    vus: int
    tours: int
    tokens_entree: int
    tokens_sortie: int
    duree: float
    modele: str

    @property
    def nb_appels_outils(self) -> int:
        """Recherches et lectures demandées par l'agent (hors amorce et sélection)."""
        return sum(a.outil in (OUTIL_RECHERCHE, OUTIL_LECTURE) for a in self.appels)


class _Etiqueteur:
    """Étiquettes courtes (P1, P2…) des passages montrés à l'agent."""

    def __init__(self) -> None:
        self.par_etiquette: dict[str, Resultat] = {}
        self._par_id: dict[Any, str] = {}

    def blocs(self, resultats: Sequence[Resultat]) -> list[dict[str, str]]:
        """Blocs texte pour l'agent : nouveaux passages en entier, déjà vus en simple rappel."""
        blocs, deja = [], []
        for r in resultats:
            if r.id in self._par_id:
                deja.append(self._par_id[r.id])
                continue
            etiquette = f"P{len(self.par_etiquette) + 1}"
            self.par_etiquette[etiquette] = r
            self._par_id[r.id] = etiquette
            renvois = f" — renvois : {', '.join(r.codes_cites)}" if r.codes_cites else ""
            blocs.append({"type": "text", "text": f"[{etiquette}] {titre_resultat(r)}{renvois}\n{r.texte}"})
        if deja:
            blocs.append({"type": "text", "text": f"Déjà fournis plus haut : {', '.join(deja)}."})
        return blocs or [{"type": "text", "text": "Aucun passage trouvé."}]


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
class AgentRecherche:
    """Boucle agentique de recherche ; renvoie une :class:`Selection`."""

    def __init__(self, client: Any, source: SourceRegles, params: ParamsAgent, prompt: PromptSysteme) -> None:
        """
        Args:
            client: client Anthropic (ou tout objet exposant ``messages.create``).
            source: moteur de recherche (dense aujourd'hui, hybride demain).
            params: section ``agent`` de la config.
            prompt: prompt chargé par :func:`charger_prompt_agent`.
        """
        self._client = client
        self._source = source
        self._params = params
        self._prompt = prompt
        self._outils = definir_outils(params.max_passages)

    def parametres_appel(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Paramètres de ``messages.create`` pour un tour (sans temperature, refusée par Sonnet 5)."""
        parametres = {
            "model": self._params.modele,
            "max_tokens": self._params.max_tokens,
            "system": self._prompt.texte,
            "tools": self._outils,
            "messages": messages,
        }
        if self._params.effort is not None:
            parametres["output_config"] = {"effort": self._params.effort}
        return parametres

    def _executer(self, bloc: Any, etiqueteur: _Etiqueteur) -> tuple[dict[str, Any], AppelOutil]:
        """Exécute un appel d'outil ; une erreur est renvoyée à l'agent, qui peut corriger son appel."""
        argument = str(bloc.input.get("requete") or bloc.input.get("code") or bloc.input)
        try:
            if bloc.name == OUTIL_RECHERCHE:
                trouves = self._source.rechercher(bloc.input["requete"], int(bloc.input.get("k", self._params.k)))
            elif bloc.name == OUTIL_LECTURE:
                trouves = self._source.lire_regle(bloc.input["code"])
            else:
                raise ValueError(f"outil inconnu : {bloc.name}")
        except Exception as e:
            logger.warning("Outil %s en erreur : %s", bloc.name, e)
            return ({"type": "tool_result", "tool_use_id": bloc.id, "content": f"Erreur : {e}", "is_error": True},
                    AppelOutil(bloc.name, argument, erreur=str(e)))
        return ({"type": "tool_result", "tool_use_id": bloc.id, "content": etiqueteur.blocs(trouves)},
                AppelOutil(bloc.name, argument, tuple(r.code for r in trouves)))

    def chercher(self, question: str, au_fil: Callable[[AppelOutil], None] | None = None) -> Selection:
        """Cherche les passages utiles à la question.

        Args:
            question: question du joueur.
            au_fil: fonction appelée à chaque étape (affichage en direct dans l'interface).

        Raises:
            ValueError: question vide.
        """
        if not question.strip():
            raise ValueError("Question vide")
        p = self._params
        notifier = au_fil or (lambda appel: None)
        etiqueteur = _Etiqueteur()
        appels: list[AppelOutil] = []
        tokens_entree = tokens_sortie = 0
        passages: list[Resultat] = []
        justification, fin = "", "repli_budget"
        debut = time.perf_counter()

        def noter(appel: AppelOutil) -> None:
            appels.append(appel)
            logger.info("Agent : %s", decrire_appel(appel))
            notifier(appel)

        contenu: list[dict[str, str]] = []
        if p.amorce:
            initiaux = self._source.rechercher(question, p.k)
            contenu.append({"type": "text", "text": "Passages trouvés par une première recherche avec la question brute :"})
            contenu += etiqueteur.blocs(initiaux)
            noter(AppelOutil("amorce", question, tuple(r.code for r in initiaux)))
        contenu.append({"type": "text", "text": f"Question du joueur : {question}"})
        messages: list[dict[str, Any]] = [{"role": "user", "content": contenu}]

        tour = 0
        for tour in range(1, p.max_tours + 1):
            reponse = self._client.messages.create(**self.parametres_appel(messages))
            tokens_entree += reponse.usage.input_tokens
            tokens_sortie += reponse.usage.output_tokens
            messages.append({"role": "assistant", "content": reponse.content})
            demandes = [b for b in reponse.content if b.type == "tool_use"]

            selection = next((b for b in demandes if b.name == OUTIL_FIN), None)
            if selection is not None:
                etiquettes = list(dict.fromkeys(selection.input.get("etiquettes", [])))  # sans doublons
                connues = [e for e in etiquettes if e in etiqueteur.par_etiquette]
                justification = selection.input.get("justification", "")
                if etiquettes and not connues:
                    fin = "repli_etiquettes"
                    noter(AppelOutil(OUTIL_FIN, ", ".join(etiquettes), erreur="étiquettes inconnues"))
                else:
                    fin = "retenue"
                    passages = [etiqueteur.par_etiquette[e] for e in connues][: p.max_passages]
                    noter(AppelOutil(OUTIL_FIN, ", ".join(etiquettes) or "(aucun)", tuple(r.code for r in passages)))
                break
            if not demandes:
                fin = "repli_texte"
                break
            if tour == p.max_tours:  # ses derniers appels ne seraient jamais lus
                break

            resultats_outils = []
            for bloc in demandes:
                resultat, appel = self._executer(bloc, etiqueteur)
                resultats_outils.append(resultat)
                noter(appel)
            if tour == p.max_tours - 1:
                resultats_outils.append({"type": "text", "text": RAPPEL_BUDGET})
            messages.append({"role": "user", "content": resultats_outils})

        if fin != "retenue":
            passages = list(etiqueteur.par_etiquette.values())[: p.max_passages]
            logger.warning("Agent : fin par repli (%s), %d passage(s) vu(s) retenu(s)", fin, len(passages))

        return Selection(
            passages=tuple(passages),
            justification=justification,
            fin=fin,
            appels=tuple(appels),
            vus=len(etiqueteur.par_etiquette),
            tours=tour,
            tokens_entree=tokens_entree,
            tokens_sortie=tokens_sortie,
            duree=time.perf_counter() - debut,
            modele=p.modele,
        )
