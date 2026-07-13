# Chat Agent — Chrome Extension

A Manifest V3 side panel extension that lets you chat with your local AI agent
and use any webpage as context with one click.

## Prerequisites

Before loading the extension, make sure the agent backend is running:

```bash
# From the repo root — start Qdrant
docker compose up qdrant -d

# From services/chat-agent/ — start the agent server
.venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8080
```

Ollama must also be running with `nomic-embed-text` pulled.

## Loading the extension in Chrome

1. Open Chrome and go to `chrome://extensions`
2. Enable **Developer mode** using the toggle in the top-right corner
3. Click **Load unpacked**
4. Select the `extension/` folder (the folder that contains `manifest.json`)
5. The "Chat Agent" extension appears in the list — no errors should be shown

## Opening the side panel

- Click the puzzle-piece icon in the Chrome toolbar → find "Chat Agent" → click it
- Or pin the extension: click the puzzle icon → click the pin next to Chat Agent →
  then click the Chat Agent icon directly in the toolbar

The side panel opens on the right side of the browser.

## Using the extension

**Chatting:**
Type a message in the input box and press Enter or click Send.
The agent replies using its conversation memory and any previously ingested documents.

**Using the current page as context:**
1. Open any webpage (Wikipedia, documentation, an article)
2. Click **📄 Use current page as context**
3. Wait for the status bar to show "✓ Page ready (N chunks)"
4. Ask questions about the page — the agent will answer using the page content

**Example workflow:**
1. Open https://en.wikipedia.org/wiki/Quantum_computing
2. Click "Use current page as context"
3. Ask: "What is quantum entanglement and how does it relate to qubits?"
4. The agent answers using the Wikipedia article content

## Reloading after code changes

If you edit any extension file, go to `chrome://extensions` → click the
reload icon (↻) next to Chat Agent. The side panel picks up the new code
on next open.

## Troubleshooting

**Side panel shows "Error: Failed to fetch"**
→ The agent server is not running. Start uvicorn (see Prerequisites).

**"Cannot read this page"**
→ You are on a `chrome://` or `chrome-extension://` page. Content scripts
  cannot run on these pages. Navigate to a normal website first.

**Extension shows errors in chrome://extensions**
→ Check the browser console (F12 → Console) while the side panel is open,
  or click "Service worker" in the extension card to inspect background.js logs.
