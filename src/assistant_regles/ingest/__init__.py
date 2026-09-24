"""Ingestion du livre de règles : PDF -> éléments structurés -> chunks.

Modules :
    config   — chargement et validation de config/ingest/*.yaml
    texte    — normalisation de texte (titres, corps)
    parse    — conversion Docling (avec cache) et attribution de la structure
    clean    — normalisation et annotation des motifs d'exclusion
    chunk    — découpage en chunks + métadonnées + JSONL
    pipeline — orchestration et point d'entrée CLI
"""
