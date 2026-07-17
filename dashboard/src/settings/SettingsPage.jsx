import { useState } from 'react';
import { IconX } from '../icons.jsx';
import { useLocalStorageState } from '../shared/useLocalStorageState.js';
import SettingsRail from './SettingsRail.jsx';
import GeneralTab from './tabs/GeneralTab.jsx';
import LlmModelsTab from './tabs/LlmModelsTab.jsx';
import EmbeddingsTab from './tabs/EmbeddingsTab.jsx';
import IntegrationsTab from './tabs/IntegrationsTab.jsx';
import AdvancedTab from './tabs/AdvancedTab.jsx';
import MarketplaceTab from './tabs/MarketplaceTab.jsx';
import '../wizard/wizard.css';
import './settings.css';

const TAB_TITLES = {
  general: 'General',
  'llm-models': 'LLM Models',
  embeddings: 'Embeddings',
  integrations: 'Integrations',
  advanced: 'Advanced',
  marketplace: 'Marketplace',
};

export default function SettingsPage({ onClose, onOpenWizard, projects, activeId, onSelectProject, onRefreshProjects, setToast }) {
  const [activeTab, setActiveTab] = useLocalStorageState('settingsTab', 'general');
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  function guardedNavigate(action) {
    if (hasUnsavedChanges && !window.confirm('You have unsaved changes in Advanced settings. Discard them?')) {
      return;
    }
    setHasUnsavedChanges(false);
    action();
  }

  function handleSelectTab(tab) {
    if (tab === activeTab) return;
    guardedNavigate(() => setActiveTab(tab));
  }

  function handleClose() {
    guardedNavigate(onClose);
  }

  function handleRerunWizard() {
    guardedNavigate(onOpenWizard);
  }

  return (
    <div className="settings-overlay" data-testid="settings-page">
      <div className="wizard-topbar">
        <span className="wizard-logo">◆ Research Agent — Settings</span>
        <div className="wizard-topbar-actions">
          <button className="icon-btn" onClick={handleClose} aria-label="Close settings" data-testid="settings-close">
            <IconX /> Close
          </button>
        </div>
      </div>

      <div className="settings-body">
        <SettingsRail activeTab={activeTab} onSelectTab={handleSelectTab} onRerunWizard={handleRerunWizard} />

        <div className="settings-content">
          <div className="settings-tab-scroll">
            <div className="wizard-step-title">
              {TAB_TITLES[activeTab]}
              {activeTab === 'advanced' && hasUnsavedChanges && (
                <span className="settings-dirty-badge">unsaved</span>
              )}
            </div>

            {activeTab === 'general' && (
              <GeneralTab
                projects={projects}
                activeId={activeId}
                onSelectProject={onSelectProject}
                onRefreshProjects={onRefreshProjects}
                setToast={setToast}
              />
            )}
            {activeTab === 'llm-models' && <LlmModelsTab />}
            {activeTab === 'embeddings' && <EmbeddingsTab />}
            {activeTab === 'integrations' && <IntegrationsTab />}
            {activeTab === 'advanced' && <AdvancedTab onDirtyChange={setHasUnsavedChanges} setToast={setToast} />}
            {activeTab === 'marketplace' && <MarketplaceTab />}
          </div>
        </div>
      </div>
    </div>
  );
}
