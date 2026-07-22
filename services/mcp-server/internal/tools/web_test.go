package tools

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// decodeResult unmarshals a ToolCallResult's text content back into the
// {query, count, results} shape textResult() produces.
func decodeResult(t *testing.T, text string) struct {
	Query   string `json:"query"`
	Count   int    `json:"count"`
	Results []struct {
		Title   string `json:"title"`
		URL     string `json:"url"`
		Snippet string `json:"snippet"`
	} `json:"results"`
} {
	t.Helper()
	var out struct {
		Query   string `json:"query"`
		Count   int    `json:"count"`
		Results []struct {
			Title   string `json:"title"`
			URL     string `json:"url"`
			Snippet string `json:"snippet"`
		} `json:"results"`
	}
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		t.Fatalf("decoding result JSON: %v\ntext: %s", err, text)
	}
	return out
}

func TestSearxngSearch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("format") != "json" {
			t.Errorf("expected format=json, got %q", r.URL.Query().Get("format"))
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"results":[
			{"title":"SearXNG result one","url":"https://example.com/1","content":"snippet one"},
			{"title":"SearXNG result two","url":"https://example.com/2","content":"snippet two"}
		]}`))
	}))
	defer srv.Close()

	res, err := searxngSearch("test query", 5, srv.URL)
	if err != nil {
		t.Fatalf("searxngSearch() error = %v", err)
	}
	if res.IsError {
		t.Fatalf("searxngSearch() returned an error result: %s", res.Content[0].Text)
	}

	got := decodeResult(t, res.Content[0].Text)
	if got.Count != 2 {
		t.Errorf("Count = %d, want 2", got.Count)
	}
	if got.Results[0].Title != "SearXNG result one" || got.Results[0].URL != "https://example.com/1" {
		t.Errorf("Results[0] = %+v, want title/url from fixture", got.Results[0])
	}
}

func TestSearxngSearchRespectsLimit(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"results":[
			{"title":"one","url":"https://example.com/1","content":""},
			{"title":"two","url":"https://example.com/2","content":""},
			{"title":"three","url":"https://example.com/3","content":""}
		]}`))
	}))
	defer srv.Close()

	res, err := searxngSearch("q", 2, srv.URL)
	if err != nil {
		t.Fatalf("searxngSearch() error = %v", err)
	}
	got := decodeResult(t, res.Content[0].Text)
	if got.Count != 2 {
		t.Errorf("Count = %d, want 2 (limit)", got.Count)
	}
}

func TestSearxngSearchNonOKStatus(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		w.Write([]byte("blocked"))
	}))
	defer srv.Close()

	res, err := searxngSearch("q", 5, srv.URL)
	if err != nil {
		t.Fatalf("searxngSearch() unexpected error = %v", err)
	}
	if !res.IsError {
		t.Fatal("expected an error result for a non-200 response")
	}
}

func TestWebSearchDispatchesToSearxngWhenConfigured(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"results":[{"title":"from searxng","url":"https://example.com","content":""}]}`))
	}))
	defer srv.Close()

	// Both SearXNG and Brave configured — SearXNG must win (higher precedence).
	t.Setenv("SEARXNG_BASE_URL", srv.URL)
	t.Setenv("BRAVE_SEARCH_API_KEY", "fake-brave-key")

	res, err := webSearch(map[string]any{"query": "test"})
	if err != nil {
		t.Fatalf("webSearch() error = %v", err)
	}
	got := decodeResult(t, res.Content[0].Text)
	if len(got.Results) != 1 || got.Results[0].Title != "from searxng" {
		t.Errorf("expected SearXNG's result to win dispatch precedence, got %+v", got.Results)
	}
}
