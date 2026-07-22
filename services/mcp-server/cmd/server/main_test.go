package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCheckOrigin(t *testing.T) {
	cases := []struct {
		name            string
		dashboardOrigin string
		requestOrigin   string
		want            bool
	}{
		{name: "no origin header allowed (server-to-server)", requestOrigin: "", want: true},
		{name: "default dev origin allowed", requestOrigin: "http://localhost:5173", want: true},
		{name: "unrecognized origin rejected", requestOrigin: "http://evil.example", want: false},
		{
			name:            "custom allowlisted origin allowed",
			dashboardOrigin: "http://localhost:5173,https://dashboard.internal",
			requestOrigin:   "https://dashboard.internal",
			want:            true,
		},
		{
			name:            "origin outside custom allowlist rejected",
			dashboardOrigin: "https://dashboard.internal",
			requestOrigin:   "http://localhost:5173",
			want:            false,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("DASHBOARD_ORIGIN", tc.dashboardOrigin)

			req := httptest.NewRequest(http.MethodGet, "/config/env", nil)
			if tc.requestOrigin != "" {
				req.Header.Set("Origin", tc.requestOrigin)
			}

			if got := checkOrigin(req); got != tc.want {
				t.Errorf("checkOrigin() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestCheckWebSearchBackend(t *testing.T) {
	t.Run("searxng configured and reachable", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
		}))
		defer srv.Close()

		t.Setenv("SEARXNG_BASE_URL", srv.URL)
		t.Setenv("BRAVE_SEARCH_API_KEY", "")

		got := checkWebSearchBackend()
		if got.Backend != "searxng" || !got.Configured {
			t.Fatalf("got %+v, want backend=searxng configured=true", got)
		}
		if got.Reachable == nil || !*got.Reachable {
			t.Errorf("Reachable = %v, want true", got.Reachable)
		}
	})

	t.Run("searxng configured but unreachable", func(t *testing.T) {
		t.Setenv("SEARXNG_BASE_URL", "http://127.0.0.1:1") // nothing listens here
		t.Setenv("BRAVE_SEARCH_API_KEY", "")

		got := checkWebSearchBackend()
		if got.Backend != "searxng" || !got.Configured {
			t.Fatalf("got %+v, want backend=searxng configured=true", got)
		}
		if got.Reachable == nil || *got.Reachable {
			t.Errorf("Reachable = %v, want false", got.Reachable)
		}
	})

	t.Run("brave configured, searxng not", func(t *testing.T) {
		t.Setenv("SEARXNG_BASE_URL", "")
		t.Setenv("BRAVE_SEARCH_API_KEY", "some-key")

		got := checkWebSearchBackend()
		if got.Backend != "brave" || !got.Configured || got.Reachable != nil {
			t.Errorf("got %+v, want backend=brave configured=true reachable=nil", got)
		}
	})

	t.Run("neither configured falls back to duckduckgo", func(t *testing.T) {
		t.Setenv("SEARXNG_BASE_URL", "")
		t.Setenv("BRAVE_SEARCH_API_KEY", "")

		got := checkWebSearchBackend()
		if got.Backend != "duckduckgo" || got.Configured || got.Reachable != nil {
			t.Errorf("got %+v, want backend=duckduckgo configured=false reachable=nil", got)
		}
	})
}
