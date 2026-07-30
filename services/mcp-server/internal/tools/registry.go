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
	mem *memoryStore
}

// NewRegistry constructs a Registry with all tools ready.
func NewRegistry() *Registry {
	return &Registry{mem: newMemoryStore()}
}

// Definitions returns the full tool list sent to MCP clients on tools/list.
func (r *Registry) Definitions() []mcp.ToolDefinition {
	defs := []mcp.ToolDefinition{}
	defs = append(defs, memoryDefinitions()...)
	defs = append(defs, webDefinitions()...)
	defs = append(defs, fileDefinitions()...)
	defs = append(defs, httpDefinitions()...)
	return defs
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
// EnvVars() reflects it immediately.
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

	default:
		return mcp.ToolCallResult{}, fmt.Errorf("unknown tool: %q", name)
	}
}
