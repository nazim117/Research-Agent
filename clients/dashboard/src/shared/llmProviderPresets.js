// Starting points for the Cloud provider setup section in LlmModelsTab.jsx —
// not env vars themselves, just client-side convenience that pre-fills the
// OPENAI_PROVIDER_LABEL/OPENAI_BASE_URL row drafts. All of these providers
// speak the same OpenAI-style /chat/completions API that
// services/chat-agent/llm.py's "openai_compatible" backend already calls,
// including Claude via Anthropic's own OpenAI-compatible endpoint — so none
// of this requires a new backend provider.
//
// baseUrl/modelPlaceholder are best-known public values, not guaranteed to
// stay current — the fields they fill are always plain editable text, and
// the placeholder text says to check the provider's own docs where model
// naming changes often.
export const CLOUD_PRESETS = [
  {
    id: 'openai',
    label: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    modelPlaceholder: 'e.g. gpt-4o-mini',
  },
  {
    id: 'anthropic',
    label: 'Claude (Anthropic)',
    baseUrl: 'https://api.anthropic.com/v1/',
    modelPlaceholder: 'e.g. claude-sonnet-5',
  },
  {
    id: 'xai',
    label: 'Grok (xAI)',
    baseUrl: 'https://api.x.ai/v1',
    modelPlaceholder: 'see docs.x.ai for current model ids',
  },
  {
    id: 'groq',
    label: 'Groq',
    baseUrl: 'https://api.groq.com/openai/v1',
    modelPlaceholder: 'see console.groq.com/docs/models',
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    modelPlaceholder: 'e.g. deepseek-chat',
  },
  {
    id: 'custom',
    label: 'Custom (OpenAI-compatible)',
    baseUrl: '',
    modelPlaceholder: '',
  },
];

export const LLM_PROVIDER_OPTIONS = [
  { value: 'ollama', label: 'Ollama (local)' },
  { value: 'openai_compatible', label: 'Cloud (OpenAI-compatible)' },
];
