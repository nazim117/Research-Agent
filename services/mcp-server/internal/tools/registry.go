// Package tools provides the MCP tools available to the agent during its
// plan → execute → summarize loop.
//
// Tools are grouped by concern:
//   - memory  — cross-step key/value store (in-process, survives within a run)
//   - web     — search and page fetching
//   - files   — read/write/list on the local filesystem
//   - http    — generic outbound HTTP for any external API
package tools

import (
	"fmt"
	"os"

	"github.com/joho/godotenv"
	"mcp-server/internal/mcp"
)

// Registry holds shared state (e.g. the memory store) and dispatches tool calls.
type Registry struct {
	mem    *memoryStore
	jira   *jiraClient   // nil if JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN not set
	github *githubClient // nil if GITHUB_TOKEN not set
}

// NewRegistry constructs a Registry with all tools ready.
// Jira and GitHub clients are only initialised when the required env vars are present.
func NewRegistry() *Registry {
	r := &Registry{mem: newMemoryStore()}
	if jiraIsConfigured() {
		r.jira = newJiraClient()
	}
	if githubIsConfigured() {
		r.github = newGitHubClient()
	}
	return r
}

// Definitions returns the full tool list sent to MCP clients on tools/list.
func (r *Registry) Definitions() []mcp.ToolDefinition {
	defs := []mcp.ToolDefinition{}
	defs = append(defs, memoryDefinitions()...)
	defs = append(defs, webDefinitions()...)
	defs = append(defs, fileDefinitions()...)
	defs = append(defs, httpDefinitions()...)
	if r.jira != nil {
		defs = append(defs, jiraDefinitions()...)
	}
	if r.github != nil {
		defs = append(defs, githubDefinitions()...)
	}
	return defs
}

// IntegrationsStatusOut reports whether Jira/GitHub are configured, for
// display in the dashboard. Never includes secrets (email, API token) —
// only configured state and the non-secret Jira base URL.
type IntegrationsStatusOut struct {
	Jira struct {
		Configured bool   `json:"configured"`
		BaseURL    string `json:"base_url"`
	} `json:"jira"`
	GitHub struct {
		Configured bool `json:"configured"`
	} `json:"github"`
}

// IntegrationsStatus reports Jira/GitHub configured state from the clients
// already constructed in NewRegistry — no env vars are re-read here, and no
// secret values (email, token) are ever included.
func (r *Registry) IntegrationsStatus() IntegrationsStatusOut {
	var out IntegrationsStatusOut
	if r.jira != nil {
		out.Jira.Configured = true
		out.Jira.BaseURL = r.jira.baseURL
	}
	out.GitHub.Configured = r.github != nil
	return out
}

// envVarSpec describes one env var this service owns for the Settings UI's
// Advanced tab. secret vars are never returned in full — only a
// last-4-characters hint once configured.
type envVarSpec struct {
	key    string
	secret bool
}

// envVarAllowlist is the fixed set of vars this service will read or write
// via EnvVars/SetEnvVar. Any key outside this list is rejected — this
// endpoint must never be usable to set arbitrary env vars.
var envVarAllowlist = []envVarSpec{
	{key: "JIRA_BASE_URL", secret: false},
	{key: "JIRA_EMAIL", secret: false},
	{key: "JIRA_API_TOKEN", secret: true},
	{key: "GITHUB_TOKEN", secret: true},
	{key: "BRAVE_SEARCH_API_KEY", secret: true},
	{key: "SEARXNG_BASE_URL", secret: false},
}

// EnvVarOut is the wire shape for one env var row in the Settings UI.
// Hint is the last 4 characters of a secret value once configured, or the
// full value for non-secret vars — never the full value of a secret.
type EnvVarOut struct {
	Key        string `json:"key"`
	Secret     bool   `json:"secret"`
	Configured bool   `json:"configured"`
	Hint       string `json:"hint,omitempty"`
}

// EnvVars reports the current state of every env var this service owns, for
// display in the Settings UI. Secret values are never returned in full.
func (r *Registry) EnvVars() []EnvVarOut {
	out := make([]EnvVarOut, 0, len(envVarAllowlist))
	for _, spec := range envVarAllowlist {
		value := os.Getenv(spec.key)
		row := EnvVarOut{Key: spec.key, Secret: spec.secret, Configured: value != ""}
		if value != "" {
			row.Hint = maskHint(value, spec.secret)
		}
		out = append(out, row)
	}
	return out
}

// maskHint returns the last 4 characters prefixed with an ellipsis for
// secrets, or the value unchanged for non-secret vars.
func maskHint(value string, secret bool) string {
	if !secret {
		return value
	}
	if len(value) <= 4 {
		return "…" + value
	}
	return "…" + value[len(value)-4:]
}

// SetEnvVar persists a new value for one allowlisted env var to the .env
// file at envPath, and updates the current process's environment so
// EnvVars() reflects it immediately. The Jira/GitHub clients already
// constructed in this Registry are unaffected until the process restarts.
func (r *Registry) SetEnvVar(key, value, envPath string) error {
	allowed := false
	for _, spec := range envVarAllowlist {
		if spec.key == key {
			allowed = true
			break
		}
	}
	if !allowed {
		return fmt.Errorf("%q is not a recognized env var", key)
	}
	if envPath == "" {
		return fmt.Errorf("no .env file found")
	}

	envMap, err := godotenv.Read(envPath)
	if err != nil {
		return fmt.Errorf("reading %s: %w", envPath, err)
	}
	envMap[key] = value
	if err := godotenv.Write(envMap, envPath); err != nil {
		return fmt.Errorf("writing %s: %w", envPath, err)
	}

	os.Setenv(key, value)
	return nil
}

// Call dispatches a tool by name and returns the result.
func (r *Registry) Call(name string, args map[string]any) (mcp.ToolCallResult, error) {
	switch name {
	// memory
	case "memory_set":
		return r.mem.set(args)
	case "memory_get":
		return r.mem.get(args)
	case "memory_list":
		return r.mem.list(args)

	// web
	case "web_search":
		return webSearch(args)
	case "web_fetch":
		return webFetch(args)

	// files
	case "file_read":
		return fileRead(args)
	case "file_write":
		return fileWrite(args)
	case "file_list":
		return fileList(args)

	// http
	case "http_request":
		return httpRequest(args)

	// jira
	case "jira_search_issues":
		if r.jira == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("jira is not configured")
		}
		return r.jira.searchIssues(args)
	case "jira_get_issue":
		if r.jira == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("jira is not configured")
		}
		return r.jira.getIssue(args)
	case "jira_add_comment":
		if r.jira == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("jira is not configured")
		}
		return r.jira.addComment(args)
	case "jira_create_issue":
		if r.jira == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("jira is not configured")
		}
		return r.jira.createIssue(args)
	case "jira_update_issue":
		if r.jira == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("jira is not configured")
		}
		return r.jira.updateIssue(args)
	case "jira_close_issue":
		if r.jira == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("jira is not configured")
		}
		return r.jira.closeIssue(args)

	// github
	case "github_list_issues":
		if r.github == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("github is not configured")
		}
		return r.github.listIssues(args)
	case "github_get_issue":
		if r.github == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("github is not configured")
		}
		return r.github.getIssue(args)
	case "github_add_comment":
		if r.github == nil {
			return mcp.ToolCallResult{}, fmt.Errorf("github is not configured")
		}
		return r.github.addComment(args)

	default:
		return mcp.ToolCallResult{}, fmt.Errorf("unknown tool: %q", name)
	}
}
