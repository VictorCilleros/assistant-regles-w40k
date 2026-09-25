-- Schéma de la base vectorielle de l'assistant-regles.
-- Idempotent : peut être rejoué sans erreur (IF NOT EXISTS partout).
-- La dimension 1024 (BGE-M3 dense) est vérifiée par le code au démarrage.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks_w40k (
    -- Champs issus du modèle Chunk de l'ingestion
    id               uuid         PRIMARY KEY,   -- uuid5 calculé par l'ingestion
    texte            text         NOT NULL,      -- texte embarqué tel quel
    chapitre         text         NOT NULL,
    section_num      text,                       -- null pour les Appendices
    section_titre    text,
    code             text,                       -- « XX.YY », non unique (15.11 en double)
    sous_section     text,
    sous_parties     text[]       NOT NULL DEFAULT '{}',
    page_debut       integer      NOT NULL,
    page_fin         integer      NOT NULL,
    type_contenu     text         NOT NULL,      -- validé en amont par Pydantic
    edition          text         NOT NULL,
    source           text         NOT NULL,
    codes_cites      text[]       NOT NULL DEFAULT '{}',
    partie           integer      NOT NULL,
    nb_parties       integer      NOT NULL,
    nb_tokens        integer      NOT NULL,
    hors_budget      boolean      NOT NULL,
    ordres           integer[]    NOT NULL,
    -- Champs propres à l'indexation
    embedding        vector(1024) NOT NULL,      -- BGE-M3 dense, normalisé
    modele_embedding text         NOT NULL,      -- nom et révision du modèle
    empreinte_texte  text         NOT NULL,      -- sha256 de `texte`
    indexe_le        timestamptz  NOT NULL DEFAULT now()
);

-- Index HNSW en distance cosinus (opérateur <=>)
CREATE INDEX IF NOT EXISTS chunks_w40k_embedding_hnsw
    ON chunks_w40k USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);