package tools

import "testing"

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
