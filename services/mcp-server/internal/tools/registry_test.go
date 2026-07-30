package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEnvVars(t *testing.T) {
	t.Setenv("BRAVE_SEARCH_API_KEY", "supersecrettoken")
	t.Setenv("SEARXNG_BASE_URL", "https://searx.example.com")

	r := NewRegistry()
	vars := r.EnvVars()

	byKey := map[string]EnvVarOut{}
	for _, v := range vars {
		byKey[v.Key] = v
	}

	if got := byKey["SEARXNG_BASE_URL"]; !got.Configured || got.Secret || got.Hint != "https://searx.example.com" {
		t.Errorf("SEARXNG_BASE_URL = %+v, want configured non-secret full value", got)
	}
	if got := byKey["BRAVE_SEARCH_API_KEY"]; !got.Configured || !got.Secret || got.Hint != "…oken" {
		t.Errorf("BRAVE_SEARCH_API_KEY = %+v, want configured secret hint '…oken'", got)
	}
}

func TestSetEnvVar(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("BRAVE_SEARCH_API_KEY=old-value\n"), 0o644); err != nil {
		t.Fatalf("seeding .env: %v", err)
	}

	r := NewRegistry()

	t.Run("rejects unknown key", func(t *testing.T) {
		if err := r.SetEnvVar("SOME_RANDOM_VAR", "x", envPath); err == nil {
			t.Fatal("expected error for unrecognized key, got nil")
		}
	})

	t.Run("rejects missing env path", func(t *testing.T) {
		if err := r.SetEnvVar("BRAVE_SEARCH_API_KEY", "x", ""); err == nil {
			t.Fatal("expected error for empty envPath, got nil")
		}
	})

	t.Run("persists allowlisted key", func(t *testing.T) {
		if err := r.SetEnvVar("BRAVE_SEARCH_API_KEY", "new-secret-value", envPath); err != nil {
			t.Fatalf("SetEnvVar() error = %v", err)
		}

		if got := os.Getenv("BRAVE_SEARCH_API_KEY"); got != "new-secret-value" {
			t.Errorf("process env BRAVE_SEARCH_API_KEY = %q, want %q", got, "new-secret-value")
		}

		persisted, err := os.ReadFile(envPath)
		if err != nil {
			t.Fatalf("reading %s: %v", envPath, err)
		}
		if !strings.Contains(string(persisted), "new-secret-value") {
			t.Errorf(".env contents = %q, want it to contain the new value", persisted)
		}
	})
}
