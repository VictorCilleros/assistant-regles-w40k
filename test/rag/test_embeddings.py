"""Tests de l'encodeur avec un faux modèle (sans torch ni téléchargement)."""

import numpy as np
import pytest

from assistant_regles.rag.config import ParamsEmbeddings
from assistant_regles.rag.embeddings import (
    EncodeurBGEM3,
    normaliser,
    resoudre_device,
    verifier_longueurs,
)

REVISION_FACTICE = "0123456789abcdef0123456789abcdef01234567"


class FauxTokenizer:
    """Un « token » par mot, plus <s> et </s>, comme le vrai tokenizer."""

    def __call__(self, textes):
        return {"input_ids": [[0] * (len(t.split()) + 2) for t in textes]}


class FauxModele:
    """Imite l'interface de SentenceTransformer utilisée par l'encodeur."""

    def __init__(self, dimension=8, longueur_max=16):
        self.tokenizer = FauxTokenizer()
        self.max_seq_length = longueur_max
        self._dimension = dimension
        self.appels_encode = []

    def get_embedding_dimension(self):
        return self._dimension

    def encode(self, textes, **kwargs):
        self.appels_encode.append(kwargs)
        # Comme le vrai modèle en fp16 : float16, normes proches de 1 sans y être.
        rng = np.random.default_rng(0)
        v = rng.normal(size=(len(textes), self._dimension))
        v = v / np.linalg.norm(v, axis=1, keepdims=True) * 1.0004
        return v.astype(np.float16)


@pytest.fixture
def params():
    return ParamsEmbeddings(revision=REVISION_FACTICE, dimension=8, batch_size=4)


# --------------------------------------------------------------------------- #
# Fonctions pures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("demande", "cuda", "attendu"),
    [("auto", True, "cuda"), ("auto", False, "cpu"), ("cpu", True, "cpu"), ("cuda", True, "cuda")],
)
def test_resoudre_device(demande, cuda, attendu):
    assert resoudre_device(demande, cuda) == attendu


def test_resoudre_device_cuda_indisponible():
    with pytest.raises(RuntimeError, match="CUDA"):
        resoudre_device("cuda", cuda_disponible=False)


def test_normaliser_float16_vers_float32_de_norme_1():
    v = np.array([[3.0, 4.0], [1.0, 1.0]], dtype=np.float16)
    resultat = normaliser(v)
    assert resultat.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(resultat, axis=1), 1.0, atol=1e-6)


def test_normaliser_refuse_vecteur_nul():
    with pytest.raises(ValueError, match="nul"):
        normaliser(np.zeros((1, 4)))


def test_normaliser_refuse_matrice_non_2d():
    with pytest.raises(ValueError, match="2D"):
        normaliser(np.ones(4))


def test_verifier_longueurs_accepte_la_limite():
    verifier_longueurs([10, 16], longueur_max=16)


def test_verifier_longueurs_signale_les_indices():
    with pytest.raises(ValueError, match=r"indices : \[1\]"):
        verifier_longueurs([10, 17, 16], longueur_max=16)


# --------------------------------------------------------------------------- #
# EncodeurBGEM3 avec un faux modèle
# --------------------------------------------------------------------------- #
def test_dimension_incoherente_refusee(params):
    with pytest.raises(ValueError, match="Dimension"):
        EncodeurBGEM3(FauxModele(dimension=16), params)


def test_identifiant(params):
    encodeur = EncodeurBGEM3(FauxModele(), params)
    assert encodeur.identifiant == f"BAAI/bge-m3@{REVISION_FACTICE}"


def test_encoder_forme_type_normes(params):
    vecteurs = EncodeurBGEM3(FauxModele(), params).encoder(["un texte", "un autre texte"])
    assert vecteurs.shape == (2, 8)
    assert vecteurs.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vecteurs, axis=1), 1.0, atol=1e-6)


def test_encoder_transmet_les_parametres(params):
    modele = FauxModele()
    EncodeurBGEM3(modele, params).encoder(["un texte"])
    (kwargs,) = modele.appels_encode
    assert kwargs["batch_size"] == 4
    assert kwargs["normalize_embeddings"] is True


def test_encoder_liste_vide(params):
    modele = FauxModele()
    vecteurs = EncodeurBGEM3(modele, params).encoder([])
    assert vecteurs.shape == (0, 8)
    assert modele.appels_encode == []


def test_encoder_refuse_texte_trop_long_avant_encodage(params):
    modele = FauxModele(longueur_max=5)
    trop_long = "un deux trois quatre"  # 4 mots + 2 tokens spéciaux = 6 > 5
    with pytest.raises(ValueError, match="tronqués"):
        EncodeurBGEM3(modele, params).encoder(["court", trop_long])
    assert modele.appels_encode == []
