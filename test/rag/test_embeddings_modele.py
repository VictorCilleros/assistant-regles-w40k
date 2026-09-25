"""Tests sur le vrai modèle BGE-M3 (marqueur ``modele``).

Exclus par défaut ; lancer avec ``uv run pytest -m modele``. Le premier lancement
télécharge environ 2,3 Go dans le cache Hugging Face. Phrases synthétiques
uniquement : aucune donnée du livre.
"""

import numpy as np
import pytest

from assistant_regles.rag.config import charger_config
from assistant_regles.rag.embeddings import EncodeurBGEM3

pytestmark = pytest.mark.modele

QUESTION = "Combien de pouces une unité peut-elle parcourir pendant son mouvement ?"
REGLE_PERTINENTE = "Une unité se déplace d'une distance au plus égale à sa caractéristique de Mouvement."
HORS_SUJET = "Faites fondre le beurre avant d'ajouter la farine."


@pytest.fixture(scope="module")
def encodeur():
    return EncodeurBGEM3.charger(charger_config().embeddings)


def test_forme_type_normes(encodeur):
    vecteurs = encodeur.encoder([QUESTION, REGLE_PERTINENTE])
    assert vecteurs.shape == (2, 1024)
    assert vecteurs.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vecteurs, axis=1), 1.0, atol=1e-5)


def test_pooling_cls(encodeur):
    """Le vecteur produit est bien celui du token <s> (pooling CLS de BGE-M3)."""
    import torch
    import torch.nn.functional as F

    modele = encodeur._modele  # accès interne assumé : on vérifie l'implémentation
    entree = modele.tokenizer([REGLE_PERTINENTE], return_tensors="pt").to(modele.device)
    with torch.no_grad():
        etats = modele[0].auto_model(**entree).last_hidden_state
    cls = F.normalize(etats[:, 0].float(), dim=-1).cpu().numpy()[0]

    officiel = encodeur.encoder([REGLE_PERTINENTE])[0]
    assert float(cls @ officiel) > 0.999


def test_ordre_semantique(encodeur):
    question, pertinente, hors_sujet = encodeur.encoder([QUESTION, REGLE_PERTINENTE, HORS_SUJET])
    assert question @ pertinente > question @ hors_sujet


def test_deterministe(encodeur):
    premier = encodeur.encoder([QUESTION])
    second = encodeur.encoder([QUESTION])
    np.testing.assert_allclose(premier, second, atol=1e-4)
