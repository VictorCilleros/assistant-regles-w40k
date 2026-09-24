"""Test de bout en bout du pipeline, sans PDF ni Docling ni tokenizer.

Un document Docling synthétique est écrit comme « cache » : le pipeline le recharge
au lieu de convertir un PDF, puis enchaîne structure, nettoyage et chunking.
"""

from __future__ import annotations

import textwrap

from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

from assistant_regles.ingest.chunk import lire_jsonl
from assistant_regles.ingest.config import charger_config
from assistant_regles.ingest.pipeline import executer


def _prov(page: int) -> ProvenanceItem:
    return ProvenanceItem(page_no=page, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, 0))


def _ecrire_config(dossier_config):
    dossier_config.mkdir(parents=True)
    (dossier_config / "pipeline.yaml").write_text(textwrap.dedent("""
        chemins:
          pdf: data/absent.pdf
          cache_docling: data/interim/cache.json
          elements: data/interim/elements.parquet
          chunks: data/interim/chunks.jsonl
        document: {source: livre_test, edition: 11e}
        nettoyage: {zones_hors_regles: ["(ouverture de chapitre)"]}
        chunking: {tokenizer: factice, cible_tokens: 400, max_tokens: 600}
    """), encoding="utf-8")
    (dossier_config / "structure_livre.yaml").write_text(textwrap.dedent("""
        chapitres:
          - titre: RÈGLES ÉLÉMENTAIRES
            page_debut: 6
            page_fin: 25
            sections:
              - {num: "01", titre: "Concepts de base", page_debut: 8}
    """), encoding="utf-8")


def test_pipeline_de_bout_en_bout(tmp_path, compter_mots):
    dossier_config = tmp_path / "config" / "ingest"
    _ecrire_config(dossier_config)
    config = charger_config(dossier_config)

    doc = DoclingDocument(name="synthetique")
    doc.add_page(page_no=8, size=Size(width=100, height=100))
    doc.add_heading("ARMÉES 01.01", prov=_prov(8))
    doc.add_text(DocItemLabel.TEXT, "Une armée est composée d'unités (voir 01.02).", prov=_prov(8))
    doc.add_text(DocItemLabel.TEXT, "8", prov=_prov(8))  # numéro de page -> exclu
    doc.add_heading("UNITÉS ET FIGURINES 01.02", prov=_prov(8))
    doc.add_text(DocItemLabel.TEXT, "Chaque unité contient des figurines.", prov=_prov(8))
    config.chemin_cache_docling.parent.mkdir(parents=True)
    doc.save_as_json(config.chemin_cache_docling)

    resultat = executer(config, compter_tokens=compter_mots)

    assert (resultat.nb_elements, resultat.nb_elements_conserves, resultat.nb_chunks) == (3, 2, 2)
    assert resultat.chemin_elements.exists()
    chunks = lire_jsonl(resultat.chemin_chunks)
    assert [c.code for c in chunks] == ["01.01", "01.02"]
    assert chunks[0].codes_cites == ["01.02"]
