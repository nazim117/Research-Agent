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
