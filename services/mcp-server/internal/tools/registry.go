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
