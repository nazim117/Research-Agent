import { useEffect, useState } from 'react';
import * as api from '../../api.js';

export default function ProjectStep({ project, onProjectChange }) {
  const [integrations, setIntegrations] = useState(null);

  useEffect(() => {
    api.getIntegrationStatus().then(setIntegrations).catch(() => setIntegrations(null));
  }, []);

  function set(field, value) {
    onProjectChange({ ...project, [field]: value });
  }

  const hasJira = Boolean(integrations?.jira?.configured);
  const hasGitHub = Boolean(integrations?.github?.configured);

  return (
    <div>
      <div className="wizard-step-title">Create your first project</div>
      <div className="wizard-step-desc">
        A project keeps its own memory, documents, and synced work items separate from
        everything else.
      </div>

      <label className="field-label">Project name</label>
      <input
        className="input"
        value={project.name}
        onChange={(e) => set('name', e.target.value)}
        placeholder="e.g. Q3 Platform Migration"
        data-testid="wizard-project-name"
      />

      {(hasJira || hasGitHub) && (
        <>
          <div className="edit-label mt-12">Link integrations (optional)</div>
          {hasJira && (
            <>
              <label className="field-label">Jira project key</label>
              <input
                className="input"
                value={project.jiraKey}
                onChange={(e) => set('jiraKey', e.target.value)}
                placeholder="e.g. KAN"
                data-testid="wizard-project-jira-key"
              />
            </>
          )}
          {hasGitHub && (
            <>
              <label className="field-label">GitHub repo</label>
              <input
                className="input"
                value={project.githubRepo}
                onChange={(e) => set('githubRepo', e.target.value)}
                placeholder="e.g. org/repo"
                data-testid="wizard-project-github-repo"
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
