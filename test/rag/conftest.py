"""Fixtures des tests rag : fabriques de données synthétiques et base éphémère.

Les imports lourds (testcontainers, psycopg) sont faits dans les fixtures :
les tests unitaires qui ne les demandent pas n'en dépendent pas.
"""

import hashlib
import uuid

import numpy as np
import pytest
import yaml

from assistant_regles.ingest.config import trouver_racine


def _fabriquer_chunk(**surcharges):
    """Chunk synthétique valide ; les champs passés en argument remplacent les défauts."""
    from assistant_regles.ingest.chunk import Chunk

    donnees = {
        "id": str(uuid.uuid4()),
        "texte": "CHAPITRE TEST > 01 Section test > 01.01 TITRE\n\nTexte synthétique.",
        "chapitre": "CHAPITRE TEST",
        "section_num": "01",
        "section_titre": "Section test",
        "code": "01.01",
        "sous_section": "TITRE",
        "sous_parties": [],
        "page_debut": 1,
        "page_fin": 1,
        "type_contenu": "regle",
        "edition": "11e",
        "source": "source_test",
        "codes_cites": [],
        "partie": 1,
        "nb_parties": 1,
        "nb_tokens": 12,
        "hors_budget": False,
        "ordres": [0],
    }
    donnees.update(surcharges)
    return Chunk.model_validate(donnees)


def _fabriquer_vecteur(graine: int, dimension: int = 1024) -> np.ndarray:
    """Vecteur aléatoire reproductible, float32, de norme 1."""
    v = np.random.default_rng(graine).normal(size=dimension).astype(np.float32)
    return v / np.linalg.norm(v)


class EncodeurFactice:
    """Respecte le contrat Encodeur sans modèle.

    Vecteur déterministe dérivé du texte : deux textes identiques donnent le même
    vecteur, comme avec le vrai modèle. ``appels`` compte les appels à ``encoder``.
    """

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.identifiant = "encodeur-factice@0"
        self.appels = 0

    def encoder(self, textes):
        self.appels += 1
        if not textes:
            return np.empty((0, self.dimension), dtype=np.float32)
        graines = [int.from_bytes(hashlib.sha256(t.encode("utf-8")).digest()[:8], "little") for t in textes]
        return np.stack([_fabriquer_vecteur(g, self.dimension) for g in graines])


@pytest.fixture
def encodeur_factice():
    return EncodeurFactice()


@pytest.fixture
def fabrique_chunk():
    return _fabriquer_chunk


@pytest.fixture
def fabrique_vecteur():
    return _fabriquer_vecteur


def _image_compose() -> str:
    """Image du service db de compose.yaml : tests et dev sur la même version."""
    with (trouver_racine() / "compose.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)["services"]["db"]["image"]


@pytest.fixture(scope="session")
def url_base():
    """Démarre un PostgreSQL + pgvector jetable pour toute la session de tests."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(_image_compose(), driver=None) as conteneur:
        yield conteneur.get_connection_url()


@pytest.fixture
def conn(url_base):
    """Connexion sur une table vide, schéma appliqué (isolation entre tests)."""
    import psycopg

    from assistant_regles.rag.store import TABLE, appliquer_schema

    with psycopg.connect(url_base, autocommit=True) as connexion:
        appliquer_schema(connexion)
        connexion.execute(f"TRUNCATE {TABLE}")
        yield connexion
