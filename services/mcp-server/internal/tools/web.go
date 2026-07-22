package tools

import (
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"mcp-server/internal/mcp"
)

var webClient = &http.Client{Timeout: 15 * time.Second}

// Compiled once at startup — used by ddgHtmlSearch.
// DDG lite uses single-quoted class attributes; href precedes the class.
var (
	// Matches: href="//duckduckgo.com/l/?uddg=...&amp;rut=..." class='result-link'>Title</a>
	reDDGLink    = regexp.MustCompile(`href="(//duckduckgo\.com/l/\?uddg=[^"]+)"[^>]*class='result-link'>([^<]+)</a>`)
	reDDGSnippet = regexp.MustCompile(`class='result-snippet'[^>]*>([\s\S]*?)</td>`)
	reHTMLTags   = regexp.MustCompile(`<[^>]*>`)
)

// webSearch dispatches to the best available search backend:
//  1. SearXNG           — when SEARXNG_BASE_URL is set (default once bundled via
//     docker-compose.yml; no API key, no per-query cost)
//  2. Brave Search API  — when BRAVE_SEARCH_API_KEY is set (free tier, requires signup)
//  3. DuckDuckGo HTML   — scraped from lite.duckduckgo.com, last-resort fallback
func webSearch(args map[string]any) (mcp.ToolCallResult, error) {
	query, errResult, err := requireString(args, "query")
	if errResult != nil {
		return *errResult, err
	}
	limit := int(optionalFloat(args, "limit", 5))
	if limit < 1 {
		limit = 1
	}
	if limit > 10 {
		limit = 10
	}

	if baseURL := os.Getenv("SEARXNG_BASE_URL"); baseURL != "" {
		return searxngSearch(query, limit, baseURL)
	}
	if apiKey := os.Getenv("BRAVE_SEARCH_API_KEY"); apiKey != "" {
		return braveSearch(query, limit, apiKey)
	}
	return ddgHtmlSearch(query, limit)
}

// searxngSearch calls a self-hosted SearXNG instance's JSON search API
// (https://docs.searxng.org/dev/search_api.html). Requires the instance to
// have "json" enabled in search.formats — see docker/searxng/settings.yml.
func searxngSearch(query string, limit int, baseURL string) (mcp.ToolCallResult, error) {
	apiURL := fmt.Sprintf(
		"%s/search?q=%s&format=json&categories=general",
		strings.TrimSuffix(baseURL, "/"), url.QueryEscape(query),
	)
	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		return textErr(fmt.Sprintf("searxng: build request: %v", err))
	}
	req.Header.Set("Accept", "application/json")

	resp, err := webClient.Do(req)
	if err != nil {
		return textErr(fmt.Sprintf("searxng: request failed: %v", err))
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return textErr(fmt.Sprintf("searxng: status %d: %s", resp.StatusCode, string(body)))
	}

	var data struct {
		Results []struct {
			Title   string `json:"title"`
			URL     string `json:"url"`
			Content string `json:"content"`
		} `json:"results"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return textErr(fmt.Sprintf("searxng: decode response: %v", err))
	}

	type result struct {
		Title   string `json:"title"`
		URL     string `json:"url"`
		Snippet string `json:"snippet"`
	}
	var results []result
	for _, r := range data.Results {
		if len(results) >= limit {
			break
		}
		results = append(results, result{Title: r.Title, URL: r.URL, Snippet: r.Content})
	}

	return textResult(map[string]any{"query": query, "count": len(results), "results": results})
}

// braveSearch calls the Brave Search API (https://api.search.brave.com).
// Free tier: 2 000 queries/month — sign up at search.brave.com/webmaster.
func braveSearch(query string, limit int, apiKey string) (mcp.ToolCallResult, error) {
	apiURL := fmt.Sprintf(
		"https://api.search.brave.com/res/v1/web/search?q=%s&count=%d&search_lang=en&result_filter=web",
		url.QueryEscape(query), limit,
	)
	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		return textErr(fmt.Sprintf("brave: build request: %v", err))
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-Subscription-Token", apiKey)

	resp, err := webClient.Do(req)
	if err != nil {
		return textErr(fmt.Sprintf("brave: request failed: %v", err))
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return textErr(fmt.Sprintf("brave: status %d: %s", resp.StatusCode, string(body)))
	}

	var data struct {
		Web struct {
			Results []struct {
				Title       string `json:"title"`
				URL         string `json:"url"`
				Description string `json:"description"`
			} `json:"results"`
		} `json:"web"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return textErr(fmt.Sprintf("brave: decode response: %v", err))
	}

	type result struct {
		Title   string `json:"title"`
		URL     string `json:"url"`
		Snippet string `json:"snippet"`
	}
	var results []result
	for _, r := range data.Web.Results {
		if len(results) >= limit {
			break
		}
		results = append(results, result{Title: r.Title, URL: r.URL, Snippet: r.Description})
	}

	return textResult(map[string]any{"query": query, "count": len(results), "results": results})
}

// ddgHtmlSearch scrapes DuckDuckGo Lite (lite.duckduckgo.com), which returns
// real web results without requiring an API key.
func ddgHtmlSearch(query string, limit int) (mcp.ToolCallResult, error) {
	req, err := http.NewRequest("GET",
		"https://lite.duckduckgo.com/lite/?q="+url.QueryEscape(query),
		nil,
	)
	if err != nil {
		return textErr(fmt.Sprintf("ddg: build request: %v", err))
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0")
	req.Header.Set("Accept", "text/html,application/xhtml+xml")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")

	resp, err := webClient.Do(req)
	if err != nil {
		return textErr(fmt.Sprintf("ddg: request failed: %v", err))
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 512*1024))
	if err != nil {
		return textErr(fmt.Sprintf("ddg: read body: %v", err))
	}
	content := string(body)

	linkMatches := reDDGLink.FindAllStringSubmatch(content, -1)
	snippetMatches := reDDGSnippet.FindAllStringSubmatch(content, -1)

	type result struct {
		Title   string `json:"title"`
		URL     string `json:"url"`
		Snippet string `json:"snippet"`
	}
	var results []result

	for i, m := range linkMatches {
		if len(results) >= limit {
			break
		}
		// m[1] = href value (still HTML-encoded), m[2] = title text
		actualURL := decodeDDGURL(html.UnescapeString(m[1]))
		if actualURL == "" {
			continue
		}
		title := cleanText(html.UnescapeString(m[2]))
		if title == "" {
			continue
		}
		snippet := ""
		if i < len(snippetMatches) {
			snippet = cleanText(html.UnescapeString(snippetMatches[i][1]))
		}
		results = append(results, result{Title: title, URL: actualURL, Snippet: snippet})
	}

	return textResult(map[string]any{"query": query, "count": len(results), "results": results})
}

// decodeDDGURL extracts the real destination URL from a DDG redirect link.
// DDG lite uses the format //duckduckgo.com/l/?uddg=<percent-encoded-url>.
func decodeDDGURL(href string) string {
	if strings.HasPrefix(href, "//") {
		href = "https:" + href
	}
	parsed, err := url.Parse(href)
	if err != nil {
		return ""
	}
	if target := parsed.Query().Get("uddg"); target != "" {
		if decoded, err := url.QueryUnescape(target); err == nil {
			return decoded
		}
		return target
	}
	// href was already an absolute URL (rare but possible)
	if strings.HasPrefix(href, "http") {
		return href
	}
	return ""
}

// cleanText strips HTML tags and collapses whitespace.
func cleanText(s string) string {
	s = reHTMLTags.ReplaceAllString(s, " ")
	return strings.TrimSpace(strings.Join(strings.Fields(s), " "))
}

// webFetch fetches the raw text content of a URL.
// It strips nothing — the agent receives the raw body. For HTML pages the
// agent should extract what it needs; for JSON APIs it will parse cleanly.
func webFetch(args map[string]any) (mcp.ToolCallResult, error) {
	rawURL, errResult, err := requireString(args, "url")
	if errResult != nil {
		return *errResult, err
	}

	// Basic URL validation
	if _, err := url.ParseRequestURI(rawURL); err != nil {
		return textErr(fmt.Sprintf("invalid URL %q: %v", rawURL, err))
	}

	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return textErr(fmt.Sprintf("build request failed: %v", err))
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; ResearchBot/1.0)")

	resp, err := webClient.Do(req)
	if err != nil {
		return textErr(fmt.Sprintf("fetch failed: %v", err))
	}
	defer resp.Body.Close()

	// Cap at 100 KB to avoid overwhelming the agent context.
	const maxBytes = 100 * 1024
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes))
	if err != nil {
		return textErr(fmt.Sprintf("read body failed: %v", err))
	}

	return textResult(map[string]any{
		"url":       rawURL,
		"status":    resp.StatusCode,
		"body":      string(body),
		"truncated": len(body) == maxBytes,
	})
}

func webDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "web_search",
			Description: "Search the web for current information and return titles, URLs, and snippets. Uses SearXNG if SEARXNG_BASE_URL is set, else Brave Search API if BRAVE_SEARCH_API_KEY is set, otherwise falls back to DuckDuckGo. Use during execute steps when the agent needs real-time or recent information.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"query": {Type: "string", Description: "The search query."},
					"limit": {Type: "number", Description: "Maximum number of results to return (default 5, max 10)."},
				},
				Required: []string{"query"},
			},
		},
		{
			Name:        "web_fetch",
			Description: "Fetch the content of a URL and return it as text. Use to read a specific page or API endpoint found via web_search. Responses are capped at 100 KB.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"url": {Type: "string", Description: "The full URL to fetch (must include https://)."},
				},
				Required: []string{"url"},
			},
		},
	}
}
