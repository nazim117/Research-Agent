const GROUPS = [
  {
    label: 'Setup',
    tabs: [
      { id: 'general', label: 'General' },
      { id: 'llm-models', label: 'LLM Models' },
      { id: 'embeddings', label: 'Embeddings' },
      { id: 'integrations', label: 'Integrations' },
    ],
  },
  {
    label: 'More',
    tabs: [
      { id: 'advanced', label: 'Advanced' },
      { id: 'marketplace', label: 'Marketplace' },
    ],
  },
];

export default function SettingsRail({ activeTab, onSelectTab }) {
  return (
    <nav className="settings-rail" aria-label="Settings sections">
      {GROUPS.map((group) => (
        <div key={group.label} className="settings-rail-group">
          <div className="settings-rail-group-label">{group.label}</div>
          <ul className="settings-rail-list">
            {group.tabs.map((tab) => {
              const current = tab.id === activeTab;
              return (
                <li key={tab.id}>
                  <button
                    type="button"
                    className={`wizard-rail-step ${current ? 'current' : ''}`.trim()}
                    onClick={() => onSelectTab(tab.id)}
                    data-testid={`settings-tab-${tab.id}`}
                  >
                    <span className="wizard-rail-dot" />
                    {tab.label}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
