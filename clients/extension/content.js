// content.js — content script injected into every tab.
//
// What is a content script?
//   Content scripts run in the context of the web page — they share the same
//   DOM as the page's own JavaScript, but they are sandboxed from the
//   extension's other scripts (background.js, sidepanel.js).  The only way
//   they communicate with the rest of the extension is via chrome.runtime.sendMessage.
//
// This script's single job: when background.js asks for the page text,
// extract it and send it back.

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "EXTRACT_TEXT") return;

  // document.body.innerText gives the visible text of the page — it strips
  // HTML tags, script blocks, style blocks, and hidden elements.
  // It is the simplest way to get "what the user can read on this page".
  let text = document.body ? document.body.innerText : "";

  // Collapse runs of blank lines and trim leading/trailing whitespace.
  text = text.replace(/\n{3,}/g, "\n\n").trim();

  // Cap at 15,000 characters.  A long Wikipedia article can be 100k+ chars,
  // which would produce ~200 chunks and make ingestion very slow.
  // 15,000 chars ≈ 30 chunks ≈ covers a typical article introduction + sections.
  const MAX_CHARS = 15000;
  if (text.length > MAX_CHARS) {
    text = text.slice(0, MAX_CHARS) + "\n\n[truncated]";
  }

  sendResponse({
    title: document.title,
    url: window.location.href,
    text,
  });

  // Returning true keeps the message channel open for the async sendResponse.
  return true;
});
