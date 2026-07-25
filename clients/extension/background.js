// background.js — service worker (runs in the background, not in any tab).
//
// Responsibilities:
//   1. Open the side panel when the user clicks the toolbar icon.
//   2. Broker GET_PAGE_TEXT messages from the side panel to the content script.
//
// "Receiving end does not exist" fix:
//   Content scripts declared in manifest.json are only auto-injected into tabs
//   that are opened AFTER the extension is loaded.  Tabs that were already open
//   when you clicked "Load unpacked" don't have the script yet.  We handle this
//   by catching the error and injecting content.js dynamically, then retrying.

chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ windowId: tab.windowId });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "GET_PAGE_TEXT") return;

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs || tabs.length === 0) {
      sendResponse({ error: "No active tab found." });
      return;
    }

    const tab = tabs[0];

    // chrome:// and other restricted URLs cannot have content scripts injected.
    if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
      sendResponse({ error: "Cannot read browser pages. Navigate to a normal website first." });
      return;
    }

    // Try the content script that may already be running.
    chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_TEXT" }, (response) => {
      if (!chrome.runtime.lastError) {
        // Content script was already there — return the result.
        sendResponse(response);
        return;
      }

      // "Receiving end does not exist" — the tab was open before the extension
      // loaded, so the content script was never auto-injected.  Inject it now.
      chrome.scripting.executeScript(
        { target: { tabId: tab.id }, files: ["content.js"] },
        () => {
          if (chrome.runtime.lastError) {
            sendResponse({ error: "Cannot inject script: " + chrome.runtime.lastError.message });
            return;
          }
          // Retry the message now that the script is injected.
          chrome.tabs.sendMessage(tab.id, { type: "EXTRACT_TEXT" }, (retryResponse) => {
            if (chrome.runtime.lastError) {
              sendResponse({ error: "Cannot read this page: " + chrome.runtime.lastError.message });
            } else {
              sendResponse(retryResponse);
            }
          });
        }
      );
    });
  });

  return true; // keep the message channel open for async work
});
