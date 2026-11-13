"""Data preparation shared by the pipelines and their tests.

The weekly-report transforms are pure Pandas with no I/O. `knowledge_base` (RAG)
is the exception: it reads the corpus in docs/company-knowledge-base/.
"""
