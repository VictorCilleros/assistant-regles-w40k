"""Encodeur d'embeddings : textes -> vecteurs denses normalisés.

Le reste du code ne dépend que du contrat :class:`Encodeur` (typage
structurel), jamais de sentence-transformers. L'implémentation BGE-M3 sépare :

- la construction (:meth:`EncodeurBGEM3.charger`) : imports lourds,
  téléchargement, choix du device, passage en fp16 ;
- la logique (:meth:`EncodeurBGEM3.encoder`) : contrôle de longueur, encodage,
  renormalisation en float32. Testable avec un faux modèle, sans torch.

Constats du notebook 03 sur la révision épinglée : pooling CLS, dimension 1024,
longueur max 8192 tokens. En fp16, les normes sortent entre 0,9997 et 1,0005,
d'où la renormalisation en float32 (format de stockage de pgvector).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

import numpy as np

from assistant_regles.rag.config import ParamsEmbeddings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class Encodeur(Protocol):
    """Contrat d'un encodeur : ce dont l'indexation et la recherche ont besoin."""

    @property
    def dimension(self) -> int:
        """Dimension des vecteurs produits."""
        ...

    @property
    def identifiant(self) -> str:
        """Nom et révision du modèle, stockés avec chaque vecteur."""
        ...

    def encoder(self, textes: Sequence[str]) -> np.ndarray:
        """Encode des textes en une matrice (n, dimension) float32 de normes 1."""
        ...


def resoudre_device(demande: str, cuda_disponible: bool) -> str:
    """Traduit le device demandé en device effectif.

    Args:
        demande: « auto », « cpu » ou « cuda ».
        cuda_disponible: résultat de ``torch.cuda.is_available()``.

    Returns:
        « cuda » ou « cpu ».

    Raises:
        RuntimeError: « cuda » demandé explicitement mais indisponible.
    """
    if demande == "auto":
        return "cuda" if cuda_disponible else "cpu"
    if demande == "cuda" and not cuda_disponible:
        raise RuntimeError("Device « cuda » demandé mais CUDA est indisponible")
    return demande


def normaliser(vecteurs: np.ndarray) -> np.ndarray:
    """Convertit en float32 et ramène chaque ligne à une norme exactement égale à 1.

    Args:
        vecteurs: matrice (n, d), quel que soit son type flottant.

    Returns:
        Matrice (n, d) float32 de normes 1.

    Raises:
        ValueError: matrice non 2D, ou vecteur nul (non normalisable).
    """
    v = np.asarray(vecteurs, dtype=np.float32)
    if v.ndim != 2:
        raise ValueError(f"Matrice 2D attendue, reçu {v.ndim} dimension(s)")
    normes = np.linalg.norm(v, axis=1, keepdims=True)
    if np.any(normes == 0):
        raise ValueError("Vecteur nul : impossible de le normaliser")
    return v / normes


def verifier_longueurs(longueurs: Sequence[int], longueur_max: int) -> None:
    """Refuse les textes que le modèle tronquerait en silence.

    Args:
        longueurs: nombre de tokens de chaque texte, tokens spéciaux compris.
        longueur_max: longueur maximale acceptée par le modèle.

    Raises:
        ValueError: au moins un texte dépasse la longueur maximale.
    """
    trop_longs = [i for i, n in enumerate(longueurs) if n > longueur_max]
    if trop_longs:
        raise ValueError(
            f"{len(trop_longs)} texte(s) dépassent {longueur_max} tokens et seraient "
            f"tronqués ; indices : {trop_longs[:10]}"
        )


class EncodeurBGEM3:
    """Encodeur BGE-M3 (sortie dense uniquement) via sentence-transformers."""

    def __init__(self, modele: SentenceTransformer, params: ParamsEmbeddings) -> None:
        """Enveloppe un modèle déjà chargé (utiliser :meth:`charger` en pratique).

        Raises:
            ValueError: la dimension du modèle diffère de celle de la config.
        """
        dimension_modele = modele.get_embedding_dimension()
        if dimension_modele != params.dimension:
            raise ValueError(
                f"Dimension du modèle ({dimension_modele}) différente de la "
                f"configuration ({params.dimension})"
            )
        self._modele = modele
        self._params = params

    @classmethod
    def charger(cls, params: ParamsEmbeddings) -> EncodeurBGEM3:
        """Charge le modèle (téléchargé au premier appel) sur le device choisi."""
        import torch
        from sentence_transformers import SentenceTransformer

        device = resoudre_device(params.device, torch.cuda.is_available())
        fp16 = params.fp16 and device == "cuda"
        if params.fp16 and not fp16:
            logger.info("fp16 ignoré : il n'est utilisé que sur GPU")
        logger.info("Chargement de %s (révision %s) sur %s, fp16=%s",params.modele, params.revision[:8], device, fp16,)
        modele = SentenceTransformer(params.modele, revision=params.revision, device=device)
        if fp16:
            modele.half()
        return cls(modele, params)

    @property
    def dimension(self) -> int:
        return self._params.dimension

    @property
    def identifiant(self) -> str:
        return f"{self._params.modele}@{self._params.revision}"

    @property
    def longueur_max(self) -> int:
        return self._modele.max_seq_length

    def encoder(self, textes: Sequence[str]) -> np.ndarray:
        """Encode des textes en une matrice (n, dimension) float32 de normes 1.

        Raises:
            ValueError: un texte dépasse la longueur maximale du modèle.
        """
        textes = list(textes)
        if not textes:
            return np.empty((0, self.dimension), dtype=np.float32)

        longueurs = [len(ids) for ids in self._modele.tokenizer(textes)["input_ids"]]
        verifier_longueurs(longueurs, self.longueur_max)

        bruts = self._modele.encode(
            textes,
            batch_size=self._params.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return normaliser(bruts)
