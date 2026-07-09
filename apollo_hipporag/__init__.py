"""
apollo_hipporag — HippoRAG 2 retrieval for Apollo's financial knowledge graph.

Architecture:
  Ingest time:  chunk → entity extraction (gx10) → triples → KG
  Query time:   query → dense retrieval → PPR expansion → merged ranked hits

The graph is stored as JSON alongside the ChromaDB directory and rebuilt
incrementally — chunks already in the graph are skipped on re-ingest.

Usage:
    from apollo_hipporag.retriever import HippoRetriever
    retriever = HippoRetriever.load(chroma_col, graph_path)
    hits = retriever.search(query, top_k=8)
"""
