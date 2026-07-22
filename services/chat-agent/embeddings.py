# embedding client for the bundled Text Embeddings Inference (TEI) service.
#
# What is an embedding?
#   An embedding is a fixed-length list of numbers (a "vector") that represents
#   the *meaning* of a piece of text.  Two texts with similar meaning will have
#   vectors that point in similar directions in high-dimensional space.
#
#   BAAI/bge-base-en-v1.5 produces a vector of 768 numbers for any input string.
#   Those 768 numbers are the model's internal representation of what the text
#   "means".  We store these vectors in Qdrant and use them for similarity search.
#
# Why a dedicated embedding server instead of Ollama?
#   TEI (https://github.com/huggingface/text-embeddings-inference) is a small,
#   purpose-built Rust server for exactly this one job — serving one embedding
#   model over HTTP — rather than a general-purpose LLM runtime. It's bundled
#   as its own docker-compose service ("embeddings") and downloads/caches its
#   configured model on first start; nothing here needs to know how to pull it.
#
# The model is intentionally fixed (docker-compose.yml's MODEL_ID), not
# user-configurable — see config.py's EMBEDDINGS_BASE_URL docstring for why.

import httpx
from fastapi import HTTPException

from config import settings


async def embed(text: str) -> list[float]:
    """Convert a text string into an embedding vector via the embeddings service.

    Args:
        text: Any string — a chat message, a document chunk, a search query.

    Returns:
        A list of 768 floats representing the semantic content of the text.

    Raises:
        HTTPException(502): If the embeddings service is unreachable or errors.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{settings.embeddings_base_url}/embed",
                json={"inputs": text},
            )
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach embeddings service at {settings.embeddings_base_url}: {exc}",
        ) from exc

    # TEI's /embed response is a list of vectors, one per input string — we
    # always send exactly one, so take the first.
    return r.json()[0]


async def get_model_info() -> dict:
    """Fetch the embeddings service's own /info — used to display the actual
    running model name in Settings, rather than trusting a chat-agent-side
    constant that could drift out of sync with the container's real config.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.embeddings_base_url}/info")
            r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach embeddings service at {settings.embeddings_base_url}: {exc}",
        ) from exc
