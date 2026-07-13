# extractors.py — turn uploaded files and pasted URLs into plain text.
#
# Each extractor returns a UTF-8 string the caller can hand to rag.ingest()
# or the transcript pipeline.  Failures raise HTTPException with a clear
# detail string so the user sees what went wrong instead of getting silent
# empty content.
#
# File handlers dispatch on extension (lowercased).  URL handlers dispatch
# on hostname.  Both keep the heavy library imports inside the handler so
# importing this module stays cheap — useful in tests that don't touch
# audio or PDF code paths.

import io
import logging
import os
import re
import tempfile
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

logger = logging.getLogger("uvicorn.error")


# ─── File extraction ──────────────────────────────────────────────────────

def _extract_txt(data: bytes) -> str:
    # errors="replace" so a stray byte never aborts the whole ingest.
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(500, f"pypdf not installed: {exc}")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except Exception as exc:
        raise HTTPException(400, f"PDF extraction failed: {exc}") from exc


def _extract_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise HTTPException(500, f"python-docx not installed: {exc}")
    try:
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        raise HTTPException(400, f"DOCX extraction failed: {exc}") from exc


# Lazy-loaded singleton: faster-whisper downloads ~150MB on first use.
# Hold the model across requests so re-using it is fast.
_WHISPER_MODEL = None


def _extract_audio(data: bytes, filename: str) -> str:
    global _WHISPER_MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise HTTPException(500, f"faster-whisper not installed: {exc}")

    if _WHISPER_MODEL is None:
        logger.info("Loading faster-whisper 'base' model — first run downloads ~150MB.")
        # int8 keeps RAM low on CPU-only machines.
        _WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8")

    # faster-whisper needs a path on disk; write to a temp file.
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".audio"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        segments, _info = _WHISPER_MODEL.transcribe(tmp_path, beam_size=1)
        return "\n".join(seg.text.strip() for seg in segments if seg.text)
    except Exception as exc:
        raise HTTPException(400, f"Audio transcription failed: {exc}") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


_FILE_HANDLERS = {
    ".txt": _extract_txt,
    ".md":  _extract_txt,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
}

_AUDIO_EXTS = {".mp3", ".wav", ".m4a"}


def extract_file(filename: str, data: bytes) -> str:
    """Dispatch by extension. Returns plain text."""
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext in _FILE_HANDLERS:
        return _FILE_HANDLERS[ext](data)
    if ext in _AUDIO_EXTS:
        return _extract_audio(data, filename)
    raise HTTPException(400, f"Unsupported file type: {ext or filename!r}")


# ─── URL extraction ───────────────────────────────────────────────────────

_YT_HOSTS = {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}


def _youtube_video_id(url: str) -> str | None:
    """Pull the 11-char video id from any YouTube URL shape we care about."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host == "youtu.be":
        return p.path.lstrip("/").split("/")[0] or None
    if "youtube.com" in host:
        m = re.search(r"[?&]v=([^&]+)", p.query)
        if m:
            return m.group(1)
        m = re.match(r"/(?:embed|v|shorts)/([^/?]+)", p.path)
        if m:
            return m.group(1)
    return None


def _extract_youtube(url: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise HTTPException(500, f"youtube-transcript-api not installed: {exc}")

    vid = _youtube_video_id(url)
    if not vid:
        raise HTTPException(400, f"Could not parse YouTube video id from {url!r}")
    try:
        # v1.x API: instantiate the client, then fetch() returns a
        # FetchedTranscript (iterable of snippets with a .text attribute) —
        # replaces the old YouTubeTranscriptApi.get_transcript() classmethod
        # that returned a list of dicts.
        fetched = YouTubeTranscriptApi().fetch(vid)
    except Exception as exc:
        # Common case: video has no captions. Surface a clear message.
        raise HTTPException(
            400,
            f"YouTube transcript fetch failed (video may have no captions): {exc}",
        ) from exc
    return "\n".join(snippet.text for snippet in fetched if snippet.text)


def _extract_wikipedia(url: str) -> str:
    """Use the MediaWiki action API for a clean plain-text extract — avoids HTML scraping."""
    p = urlparse(url)
    title = p.path.rsplit("/", 1)[-1]
    if not title:
        raise HTTPException(400, f"Could not parse Wikipedia title from {url!r}")
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.get(
                f"https://{p.hostname}/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "titles": title,
                    "prop": "extracts",
                    "explaintext": "1",
                    "redirects": "1",
                },
            )
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            if not pages:
                raise HTTPException(400, f"No Wikipedia page found for {title!r}")
            page = next(iter(pages.values()))
            text = (page.get("extract") or "").strip()
            if not text:
                raise HTTPException(400, f"Wikipedia returned empty extract for {title!r}")
            return text
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Wikipedia fetch failed: {exc}") from exc


def _extract_generic_url(url: str) -> str:
    try:
        import trafilatura
    except ImportError as exc:
        raise HTTPException(500, f"trafilatura not installed: {exc}")
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": "ProjectBrain/1.0"})
            r.raise_for_status()
            html = r.text
        text = trafilatura.extract(html) or ""
        if not text.strip():
            raise HTTPException(400, f"No extractable content at {url!r}")
        return text
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"URL fetch/extract failed: {exc}") from exc


def extract_url(url: str) -> tuple[str, str]:
    """Return (source_label, text). source_label is human-friendly."""
    p = urlparse(url)
    if not p.scheme or not p.hostname:
        raise HTTPException(400, f"Invalid URL: {url!r}")

    host = p.hostname.lower()
    if host in _YT_HOSTS:
        text = _extract_youtube(url)
        vid = _youtube_video_id(url) or "video"
        return (f"youtube:{vid}", text)
    if host.endswith("wikipedia.org"):
        text = _extract_wikipedia(url)
        title = p.path.rsplit("/", 1)[-1] or host
        return (f"wikipedia:{title}", text)

    text = _extract_generic_url(url)
    return (url, text)
