package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestIntegrationsStatus(t *testing.T) {
	cases := []struct {
		name        string
		jiraBaseURL string
		jiraEmail   string
		jiraToken   string
		githubToken string
		wantJira    bool
		wantJiraURL string
		wantGitHub  bool
	}{
		{
			name:        "nothing configured",
			wantJira:    false,
			wantJiraURL: "",
			wantGitHub:  false,
		},
		{
			name:        "jira fully configured",
			jiraBaseURL: "https://example.atlassian.net/",
			jiraEmail:   "pm@example.com",
			jiraToken:   "secret-token",
			wantJira:    true,
			wantJiraURL: "https://example.atlassian.net", // trailing slash trimmed
			wantGitHub:  false,
		},
		{
			name:        "jira partially configured does not count",
			jiraBaseURL: "https://example.atlassian.net",
			jiraEmail:   "pm@example.com",
			// no token
			wantJira:    false,
			wantJiraURL: "",
			wantGitHub:  false,
		},
		{
			name:        "github configured",
			githubToken: "ghp_secret",
			wantJira:    false,
			wantGitHub:  true,
		},
		{
			name:        "both configured",
			jiraBaseURL: "https://example.atlassian.net",
			jiraEmail:   "pm@example.com",
			jiraToken:   "secret-token",
			githubToken: "ghp_secret",
			wantJira:    true,
			wantJiraURL: "https://example.atlassian.net",
			wantGitHub:  true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("JIRA_BASE_URL", tc.jiraBaseURL)
			t.Setenv("JIRA_EMAIL", tc.jiraEmail)
			t.Setenv("JIRA_API_TOKEN", tc.jiraToken)
			t.Setenv("GITHUB_TOKEN", tc.githubToken)

			r := NewRegistry()
			status := r.IntegrationsStatus()

			if status.Jira.Configured != tc.wantJira {
				t.Errorf("Jira.Configured = %v, want %v", status.Jira.Configured, tc.wantJira)
			}
			if status.Jira.BaseURL != tc.wantJiraURL {
				t.Errorf("Jira.BaseURL = %q, want %q", status.Jira.BaseURL, tc.wantJiraURL)
			}
			if status.GitHub.Configured != tc.wantGitHub {
				t.Errorf("GitHub.Configured = %v, want %v", status.GitHub.Configured, tc.wantGitHub)
			}

			// Never leak secrets: neither field name should exist in JSON marshaling
			// (compile-time enforced by the struct not having those fields at all —
			// this test documents that intent for future readers).
			_ = tc.jiraEmail
			_ = tc.jiraToken
			_ = tc.githubToken
		})
	}
}

func TestEnvVars(t *testing.T) {
	t.Setenv("JIRA_BASE_URL", "https://example.atlassian.net")
	t.Setenv("JIRA_EMAIL", "")
	t.Setenv("JIRA_API_TOKEN", "supersecrettoken")
	t.Setenv("GITHUB_TOKEN", "")
	t.Setenv("BRAVE_SEARCH_API_KEY", "")

	r := NewRegistry()
	vars := r.EnvVars()

	byKey := map[string]EnvVarOut{}
	for _, v := range vars {
		byKey[v.Key] = v
	}

	if got := byKey["JIRA_BASE_URL"]; !got.Configured || got.Secret || got.Hint != "https://example.atlassian.net" {
		t.Errorf("JIRA_BASE_URL = %+v, want configured non-secret full value", got)
	}
	if got := byKey["JIRA_EMAIL"]; got.Configured {
		t.Errorf("JIRA_EMAIL = %+v, want not configured", got)
	}
	if got := byKey["JIRA_API_TOKEN"]; !got.Configured || !got.Secret || got.Hint != "…oken" {
		t.Errorf("JIRA_API_TOKEN = %+v, want configured secret hint '…oken'", got)
	}
	if got := byKey["GITHUB_TOKEN"]; got.Configured || got.Hint != "" {
		t.Errorf("GITHUB_TOKEN = %+v, want not configured with no hint", got)
	}
}

func TestSetEnvVar(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("GITHUB_TOKEN=old-value\n"), 0o644); err != nil {
		t.Fatalf("seeding .env: %v", err)
	}

	r := NewRegistry()

	t.Run("rejects unknown key", func(t *testing.T) {
		if err := r.SetEnvVar("SOME_RANDOM_VAR", "x", envPath); err == nil {
			t.Fatal("expected error for unrecognized key, got nil")
		}
	})

	t.Run("rejects missing env path", func(t *testing.T) {
		if err := r.SetEnvVar("GITHUB_TOKEN", "x", ""); err == nil {
			t.Fatal("expected error for empty envPath, got nil")
		}
	})

	t.Run("persists allowlisted key", func(t *testing.T) {
		if err := r.SetEnvVar("GITHUB_TOKEN", "new-secret-value", envPath); err != nil {
			t.Fatalf("SetEnvVar() error = %v", err)
		}

		if got := os.Getenv("GITHUB_TOKEN"); got != "new-secret-value" {
			t.Errorf("process env GITHUB_TOKEN = %q, want %q", got, "new-secret-value")
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
