# local embedding model client via Ollama.
#
# What is an embedding?
#   An embedding is a fixed-length list of numbers (a "vector") that represents
#   the *meaning* of a piece of text.  Two texts with similar meaning will have
#   vectors that point in similar directions in high-dimensional space.
#
#   nomic-embed-text produces a vector of 768 numbers for any input string.
#   Those 768 numbers are the model's internal representation of what the text
#   "means".  We store these vectors in Qdrant and use them for similarity search.
#
# Why Ollama?
#   Ollama runs the model locally on your machine — no API calls, no cost per
#   request, no data leaving your computer.  It exposes a simple HTTP API that
#   the official `ollama` Python package wraps for us.
#
# Why async?
#   Calling Ollama is a network + CPU operation.  Using the async client means
#   FastAPI can handle other requests while waiting for the embedding to compute,
#   rather than freezing the entire process.

import ollama
from fastapi import HTTPException

from config import settings


async def embed(text: str) -> list[float]:
    """Convert a text string into an embedding vector using the local Ollama model.

    Args:
        text: Any string — a chat message, a document chunk, a search query.

    Returns:
        A list of 768 floats representing the semantic content of the text.
        (768 is the output dimension of nomic-embed-text.)

    Raises:
        HTTPException(502): If Ollama is unreachable or returns an error.
    """
    try:
        client = ollama.AsyncClient(host=settings.ollama_base_url)
        # embed() is the modern Ollama API for embeddings (replaces embeddings()).
        # It returns an EmbedResponse object; .embeddings is a list of vectors
        # (one per input string).  We pass a single string so we take index [0].
        response = await client.embed(
            model=settings.ollama_embed_model,
            input=text,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Ollama at {settings.ollama_base_url}: {exc}",
        ) from exc

    return response.embeddings[0]
