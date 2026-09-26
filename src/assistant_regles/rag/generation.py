"""Génération : question + chunks retrouvés -> réponse de Claude, citée.

Lancement manuel : ``uv run repondre-regles "ma question" [-k 5]``.

Les chunks sont transmis en blocs ``search_result`` de l'API Messages, avec les
citations activées : chaque segment de la réponse qui s'appuie sur un extrait
porte des citations structurées (résultat cité et texte exact). Une citation ne
peut donc pointer que vers un chunk réellement fourni.

Choix issus du notebook 06 :

- granularité ``paragraphe`` : un bloc de texte par paragraphe, pour que les
  citations ciblent les passages précis (y compris les cas particuliers) ;
- effort ``medium`` : précisions utiles absentes en ``low``, latence identique ;
- pas de ``temperature`` : Claude Sonnet 5 refuse les valeurs non par défaut.

Deux modes : :meth:`Generateur.generer` (réponse complète) et
:meth:`Generateur.generer_en_flux` (texte au fil de l'eau, pour l'interface).
Les deux produisent la même :class:`Reponse`, via :func:`lire_reponse`.

Traçabilité : chaque :class:`Reponse` porte le nom du prompt système et son
empreinte sha256, pour savoir exactement quel texte l'a produite même si le
fichier est modifié entre deux exécutions.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from assistant_regles.rag.config import ParamsGeneration
    from assistant_regles.rag.recherche import Resultat

logger = logging.getLogger(__name__)

DOSSIER_PROMPTS = "prompts"
CARACTERES_AVANT_ABSTENTION = " \t\n«\"'"  # ignorés avant la phrase d'abstention


# --------------------------------------------------------------------------- #
# Prompt système
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PromptSysteme:
    """Texte du prompt système, avec son nom de fichier et son empreinte."""

    nom: str
    texte: str
    empreinte: str  # sha256 hexadécimal du texte


def verifier_prompt(texte: str, phrase_abstention: str) -> None:
    """Refuse un prompt qui ne contient pas la phrase d'abstention attendue.

    Sans elle, la détection automatique des abstentions ne fonctionnerait plus.

    Raises:
        ValueError: phrase absente du prompt.
    """
    if phrase_abstention not in texte:
        raise ValueError(
            "Le prompt système ne contient pas la phrase d'abstention de la config "
            f"(« {phrase_abstention} ») : les mettre en cohérence"
        )


def charger_prompt(nom: str, phrase_abstention: str) -> PromptSysteme:
    """Lit un prompt système depuis ``rag/prompts/`` et le vérifie.

    Raises:
        FileNotFoundError: fichier absent (le message liste les prompts disponibles).
        ValueError: phrase d'abstention absente du prompt.
    """
    dossier = resources.files("assistant_regles.rag").joinpath(DOSSIER_PROMPTS)
    fichier = dossier.joinpath(nom)
    if not fichier.is_file():
        disponibles = sorted(f.name for f in dossier.iterdir() if f.name.endswith(".md"))
        raise FileNotFoundError(f"Prompt « {nom} » introuvable ; disponibles : {disponibles}")
    texte = fichier.read_text(encoding="utf-8")
    verifier_prompt(texte, phrase_abstention)
    return PromptSysteme(nom, texte, hashlib.sha256(texte.encode("utf-8")).hexdigest())


# --------------------------------------------------------------------------- #
# Résultats de recherche -> message utilisateur
# --------------------------------------------------------------------------- #
def titre_resultat(r: Resultat) -> str:
    """Titre lisible d'un chunk : code, sous-section, pages."""
    pages = f"p. {r.page_debut}" + (f"-{r.page_fin}" if r.page_fin != r.page_debut else "")
    return f"{r.code or '(sans code)'} {r.sous_section or r.section_titre or ''} — {pages}".replace("  ", " ")


def decouper(texte: str, granularite: str) -> list[str]:
    """Découpe le texte d'un chunk en blocs citables (les blocs vides sont écartés)."""
    if granularite == "paragraphe":
        return [p.strip() for p in texte.split("\n\n") if p.strip()]
    return [texte]


def vers_search_result(r: Resultat, granularite: str) -> dict[str, Any]:
    """Bloc ``search_result`` de l'API pour un chunk, citations activées."""
    return {
        "type": "search_result",
        "source": f"chunk:{r.id}",
        "title": titre_resultat(r),
        "content": [{"type": "text", "text": bloc} for bloc in decouper(r.texte, granularite)],
        "citations": {"enabled": True},
    }


def construire_message(question: str, resultats: Sequence[Resultat], granularite: str) -> dict[str, Any]:
    """Message utilisateur : les extraits d'abord, la question ensuite."""
    return {
        "role": "user",
        "content": [vers_search_result(r, granularite) for r in resultats]
        + [{"type": "text", "text": question}],
    }


# --------------------------------------------------------------------------- #
# Réponse
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Citation:
    """Passage d'un chunk cité par Claude."""

    rang_resultat: int  # rang du chunk dans la recherche (1 = le plus proche)
    code: str | None
    titre: str
    texte_cite: str
    bloc_debut: int
    bloc_fin: int  # exclusif


@dataclass(frozen=True)
class Segment:
    """Morceau de texte de la réponse, avec les citations qui l'étayent."""

    texte: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class Reponse:
    """Réponse générée, avec sa provenance et ses métadonnées d'exécution."""

    question: str
    segments: tuple[Segment, ...]
    resultats: tuple[Resultat, ...]
    abstention: bool
    stop_reason: str | None
    modele: str
    prompt: str
    empreinte_prompt: str
    tokens_entree: int
    tokens_sortie: int
    duree: float

    @property
    def texte(self) -> str:
        """Texte complet de la réponse, sans les citations."""
        return "".join(s.texte for s in self.segments)

    @property
    def citations(self) -> list[Citation]:
        """Citations distinctes, dans leur ordre d'apparition."""
        vues: dict[tuple[int, int, int], Citation] = {}
        for segment in self.segments:
            for c in segment.citations:
                vues.setdefault((c.rang_resultat, c.bloc_debut, c.bloc_fin), c)
        return list(vues.values())

    @property
    def part_citee(self) -> float:
        """Part du texte (en caractères) portée par des segments cités."""
        total = sum(len(s.texte) for s in self.segments)
        cite = sum(len(s.texte) for s in self.segments if s.citations)
        return cite / total if total else 0.0


def est_abstention(texte: str, phrase_abstention: str) -> bool:
    """Vrai si la réponse commence par la phrase d'abstention (guillemets tolérés)."""
    return texte.lstrip(CARACTERES_AVANT_ABSTENTION).startswith(phrase_abstention)


def lire_reponse(
    message: Any,
    question: str,
    resultats: Sequence[Resultat],
    params: ParamsGeneration,
    prompt: PromptSysteme,
    duree: float,
) -> Reponse:
    """Convertit la réponse de l'API en :class:`Reponse`.

    Ignore les blocs de réflexion ; relie chaque citation au chunk cité grâce à
    ``search_result_index`` (position du bloc dans la requête = rang - 1).
    Fonction séparée de l'appel pour pouvoir traiter aussi, plus tard, le
    message final d'une réponse en streaming.
    """
    segments = []
    for bloc in message.content:
        if bloc.type != "text":
            continue
        citations = []
        for c in bloc.citations or []:
            if getattr(c, "type", None) != "search_result_location":
                continue
            if not 0 <= c.search_result_index < len(resultats):
                raise ValueError(f"Citation vers un résultat inexistant : index {c.search_result_index}")
            cite = resultats[c.search_result_index]
            citations.append(Citation(
                rang_resultat=c.search_result_index + 1, code=cite.code, titre=c.title or titre_resultat(cite),
                texte_cite=c.cited_text, bloc_debut=c.start_block_index, bloc_fin=c.end_block_index,
            ))
        segments.append(Segment(bloc.text, tuple(citations)))

    if message.stop_reason == "max_tokens":
        logger.warning("Réponse tronquée (max_tokens=%d atteint)", params.max_tokens)
    elif message.stop_reason == "refusal":
        logger.warning("Le modèle a refusé de répondre (stop_reason=refusal)")

    texte = "".join(s.texte for s in segments)
    return Reponse(
        question=question,
        segments=tuple(segments),
        resultats=tuple(resultats),
        abstention=est_abstention(texte, params.phrase_abstention),
        stop_reason=message.stop_reason,
        modele=params.modele,
        prompt=prompt.nom,
        empreinte_prompt=prompt.empreinte,
        tokens_entree=message.usage.input_tokens,
        tokens_sortie=message.usage.output_tokens,
        duree=duree,
    )


class FluxReponse:
    """Réponse en streaming : itérer donne le texte au fil de l'eau.

    Une fois l'itération terminée, :attr:`reponse` contient la :class:`Reponse`
    complète (citations comprises), construite à partir du message final.
    Utilisable directement avec ``st.write_stream``.
    """

    def __init__(
        self,
        client: Any,
        parametres: dict[str, Any],
        question: str,
        resultats: Sequence[Resultat],
        params: ParamsGeneration,
        prompt: PromptSysteme,
    ) -> None:
        self._client = client
        self._parametres = parametres
        self._question = question
        self._resultats = resultats
        self._params = params
        self._prompt = prompt
        self.reponse: Reponse | None = None

    def __iter__(self) -> Iterator[str]:
        debut = time.perf_counter()
        with self._client.messages.stream(**self._parametres) as flux:
            yield from flux.text_stream  # seulement le texte : la réflexion n'est pas diffusée
            message = flux.get_final_message()
        self.reponse = lire_reponse(
            message, self._question, self._resultats, self._params, self._prompt,
            time.perf_counter() - debut,
        )


class Generateur:
    """Génère une réponse citée à partir d'une question et des chunks retrouvés."""

    def __init__(self, client: Any, params: ParamsGeneration, prompt: PromptSysteme) -> None:
        """
        Args:
            client: client Anthropic (ou tout objet exposant ``messages.create``).
            params: section ``generation`` de la config.
            prompt: prompt système chargé par :func:`charger_prompt`.
        """
        self._client = client
        self._params = params
        self._prompt = prompt

    def parametres_requete(self, question: str, resultats: Sequence[Resultat]) -> dict[str, Any]:
        """Paramètres de l'appel ``messages.create`` (sans temperature : refusée par Sonnet 5)."""
        parametres = {
            "model": self._params.modele,
            "max_tokens": self._params.max_tokens,
            "system": self._prompt.texte,
            "messages": [construire_message(question, resultats, self._params.granularite)],
            "output_config": {"effort": self._params.effort},
        }
        if not self._params.reflexion:
            parametres["thinking"] = {"type": "disabled"}
        return parametres

    @staticmethod
    def _valider(question: str, resultats: Sequence[Resultat]) -> None:
        if not question.strip():
            raise ValueError("Question vide")
        if not resultats:
            raise ValueError("Aucun chunk fourni : la recherche n'a rien renvoyé")

    def generer_en_flux(self, question: str, resultats: Sequence[Resultat]) -> FluxReponse:
        """Prépare une réponse en streaming (l'appel part à la première itération).

        Raises:
            ValueError: question vide ou aucun chunk fourni.
        """
        self._valider(question, resultats)
        return FluxReponse(
            self._client, self.parametres_requete(question, resultats),
            question, resultats, self._params, self._prompt,
        )

    def generer(self, question: str, resultats: Sequence[Resultat]) -> Reponse:
        """Appelle Claude et renvoie la réponse structurée.

        Raises:
            ValueError: question vide ou aucun chunk fourni.
        """
        self._valider(question, resultats)
        debut = time.perf_counter()
        message = self._client.messages.create(**self.parametres_requete(question, resultats))
        duree = time.perf_counter() - debut
        reponse = lire_reponse(message, question, resultats, self._params, self._prompt, duree)
        logger.info(
            "Réponse en %.1f s (%d tokens en entrée, %d en sortie), %d citation(s), abstention=%s",
            duree, reponse.tokens_entree, reponse.tokens_sortie, len(reponse.citations), reponse.abstention,
        )
        return reponse


# --------------------------------------------------------------------------- #
# Affichage et CLI
# --------------------------------------------------------------------------- #
def formater_reponse(reponse: Reponse, largeur_extrait: int = 160) -> str:
    """Réponse avec notes numérotées [1], [2]…, liste des sources et pied technique."""
    numeros: dict[tuple[int, int, int], int] = {}
    corps = []
    for segment in reponse.segments:
        corps.append(segment.texte)
        for c in segment.citations:
            cle = (c.rang_resultat, c.bloc_debut, c.bloc_fin)
            numeros.setdefault(cle, len(numeros) + 1)
            corps.append(f" [{numeros[cle]}]")

    lignes = ["".join(corps).strip(), ""]
    if reponse.citations:
        lignes.append("Sources :")
        for c in reponse.citations:
            extrait = " ".join(c.texte_cite.split())
            if len(extrait) > largeur_extrait:
                extrait = extrait[:largeur_extrait].rstrip() + " …"
            lignes.append(f"[{numeros[(c.rang_resultat, c.bloc_debut, c.bloc_fin)]}] {c.titre}")
            lignes.append(f"    « {extrait} »")
        lignes.append("")
    lignes.append(
        f"({reponse.modele}, prompt {reponse.prompt} [{reponse.empreinte_prompt[:8]}], "
        f"{reponse.duree:.1f} s, {reponse.tokens_entree} tokens en entrée, "
        f"{reponse.tokens_sortie} en sortie, {reponse.part_citee:.0%} du texte cité)"
    )
    return "\n".join(lignes)


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée de ``uv run repondre-regles`` : recherche puis génération.

    Returns:
        Code de sortie : 0 si une réponse a été produite, 1 sinon.
    """
    parser = argparse.ArgumentParser(description="Répond à une question de règles, avec citations.")
    parser.add_argument("question", help="question en langage naturel (entre guillemets)")
    parser.add_argument("-k", type=int, default=None, help="nombre de chunks transmis (défaut : config)")
    parser.add_argument("-v", "--verbeux", action="store_true", help="journalisation INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbeux else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    import os

    import anthropic
    import psycopg
    from dotenv import load_dotenv

    from assistant_regles.ingest.config import trouver_racine
    from assistant_regles.rag.config import charger_config
    from assistant_regles.rag.embeddings import EncodeurBGEM3, identifiant_modele
    from assistant_regles.rag.recherche import (
        MoteurRecherche,
        valider_requete,
        verifier_base,
    )

    load_dotenv(trouver_racine() / ".env")
    config = charger_config()
    k = args.k if args.k is not None else config.recherche.k

    # Tout ce qui peut échouer vite est vérifié avant de charger le modèle d'embedding
    try:
        valider_requete(args.question, k)
        prompt = charger_prompt(config.generation.prompt_systeme, config.generation.phrase_abstention)
        # Le client ne vérifie la clé qu'au premier appel : on le fait ici, avant le modèle
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY absente : la renseigner dans le .env")
        client = anthropic.Anthropic()
    except (ValueError, FileNotFoundError, anthropic.AnthropicError) as e:
        logger.error("%s", e)
        return 1

    try:
        conn = psycopg.connect(autocommit=True)
    except psycopg.OperationalError as e:
        logger.error("Base injoignable (`docker compose up -d` ?) : %s", e)
        return 1

    with conn:
        try:
            verifier_base(conn, identifiant_modele(config.embeddings))
            moteur = MoteurRecherche(EncodeurBGEM3.charger(config.embeddings), conn)
            resultats = moteur.rechercher(args.question, k)
            reponse = Generateur(client, config.generation, prompt).generer(args.question, resultats)
        except (RuntimeError, ValueError, anthropic.APIError) as e:
            logger.error("%s", e)
            return 1

    print(f"Question : {args.question}\n")
    print(formater_reponse(reponse))
    return 0
