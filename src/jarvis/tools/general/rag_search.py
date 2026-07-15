"""Tool for retrieving relevant context from the document store."""
from langchain_core.tools import tool

from jarvis.memory.retrieve import query_context


@tool
def rag_search(query: str) -> str:
    """Search stored documents for information relevant to *query*.

    Use this when you need specific context from previously ingested
    documents — for example user manuals, notes, or reference material.
    Returns a plain-text block of the most relevant excerpts (or an
    empty string if nothing is found).
    """
    return query_context(query, k=4)
