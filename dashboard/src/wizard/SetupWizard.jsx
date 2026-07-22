import { useState } from 'react';
import * as api from '../api.js';
import { IconX } from '../icons.jsx';
import WizardRail from './WizardRail.jsx';
import WelcomeStep from './steps/WelcomeStep.jsx';
import HealthCheckStep from './steps/HealthCheckStep.jsx';
import ModelsStep from './steps/ModelsStep.jsx';
import CredentialsStep from './steps/CredentialsStep.jsx';
import ProjectStep from './steps/ProjectStep.jsx';
import SuccessStep from './steps/SuccessStep.jsx';
import './wizard.css';

export const PROGRESS_KEY = 'wizardProgress';
export const STATUS_KEY = 'onboardingStatus';

const STEPS = ['welcome', 'health', 'models', 'credentials', 'project'];

const DEFAULT_PROGRESS = {
  step: 'welcome',
  completedSteps: [],
  health: null,
  models: { chat: null },
  project: { name: '', jiraKey: '', githubRepo: '' },
};

function loadProgress() {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY);
    if (!raw) return DEFAULT_PROGRESS;
    return { ...DEFAULT_PROGRESS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_PROGRESS;
  }
}

// localStorage can throw (private browsing, quota exceeded, disabled storage) —
// swallow so a storage failure degrades to "doesn't persist" rather than
// crashing the wizard (there's no ErrorBoundary above it).
function trySetItem(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore — state still updates in memory
  }
}

function isStepValid(step, progress) {
  switch (step) {
    case 'welcome':
      return true;
    case 'health':
      return Boolean(progress.health) &&
        Object.values(progress.health).every((s) => s.status === 'ok' || !s.required);
    case 'models':
      return Boolean(progress.models.chat);
    case 'credentials':
      return true;
    case 'project':
      return Boolean(progress.project.name.trim());
    default:
      return false;
  }
}

export default function SetupWizard({ onExit, onProjectCreated }) {
  const [progress, setProgressState] = useState(loadProgress);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  function setProgress(update) {
    setProgressState((prev) => {
      const next = typeof update === 'function' ? update(prev) : update;
      trySetItem(PROGRESS_KEY, JSON.stringify(next));
      return next;
    });
  }

  function handleExit(status) {
    trySetItem(STATUS_KEY, status);
    onExit();
  }

  function handleBack() {
    setProgress((p) => {
      const idx = STEPS.indexOf(p.step);
      if (idx <= 0) return p;
      return { ...p, step: STEPS[idx - 1] };
    });
  }

  async function handleNext() {
    if (progress.step === 'project') {
      await handleFinishProject();
      return;
    }
    setProgress((p) => {
      const idx = STEPS.indexOf(p.step);
      const completedSteps = p.completedSteps.includes(p.step)
        ? p.completedSteps
        : [...p.completedSteps, p.step];
      return { ...p, completedSteps, step: STEPS[idx + 1] };
    });
  }

  async function handleFinishProject() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const refs = {};
      if (progress.project.jiraKey) refs.jira_project_key = progress.project.jiraKey.trim();
      if (progress.project.githubRepo) refs.github_repo = progress.project.githubRepo.trim();
      await api.createProject(progress.project.name.trim(), refs);
      onProjectCreated?.();
      setProgress((p) => ({ ...p, completedSteps: STEPS, step: 'success' }));
      trySetItem(STATUS_KEY, 'completed');
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  const valid = isStepValid(progress.step, progress);
  const stepIdx = STEPS.indexOf(progress.step);
  const requiredDown = progress.health &&
    Object.values(progress.health).some((s) => s.required && s.status !== 'ok');
  const optionalDown = progress.health &&
    Object.values(progress.health).some((s) => !s.required && s.status !== 'ok');

  let stepContent = null;
  let nextLabel = 'Next →';
  if (progress.step === 'welcome') {
    stepContent = <WelcomeStep />;
    nextLabel = 'Get Started →';
  } else if (progress.step === 'health') {
    stepContent = (
      <HealthCheckStep
        health={progress.health}
        onHealthChange={(health) => setProgress((p) => ({ ...p, health }))}
      />
    );
  } else if (progress.step === 'models') {
    stepContent = (
      <ModelsStep
        models={progress.models}
        onModelsChange={(models) => setProgress((p) => ({ ...p, models }))}
      />
    );
  } else if (progress.step === 'credentials') {
    stepContent = <CredentialsStep />;
  } else if (progress.step === 'project') {
    stepContent = (
      <ProjectStep
        project={progress.project}
        onProjectChange={(project) => setProgress((p) => ({ ...p, project }))}
      />
    );
    nextLabel = 'Finish setup ✓';
  }

  return (
    <div className="wizard-overlay" data-testid="setup-wizard">
      <div className="wizard-topbar">
        <span className="wizard-logo">◆ Research Agent</span>
        {progress.step !== 'success' && (
          <div className="wizard-topbar-actions">
            <a className="wizard-link" href="#" onClick={(e) => e.preventDefault()}>Help</a>
            <button className="wizard-link" onClick={() => handleExit('skipped')} data-testid="wizard-skip">
              Skip setup
            </button>
            <button className="icon-btn" onClick={() => handleExit('skipped')} aria-label="Close setup wizard">
              <IconX />
            </button>
          </div>
        )}
      </div>

      <div className="wizard-body">
        <div className="wizard-shell">
          {progress.step !== 'success' && (
            <WizardRail steps={STEPS} currentStep={progress.step} completedSteps={progress.completedSteps} />
          )}
          <div className="wizard-content">
            {progress.step === 'success' ? (
              <SuccessStep
                health={progress.health}
                models={progress.models}
                project={progress.project}
                onFinish={() => handleExit('completed')}
              />
            ) : (
              <>
                <div className="wizard-step-scroll">
                  {stepContent}
                  {progress.step === 'health' && requiredDown && (
                    <div className="wizard-warning">
                      All required services must be healthy before continuing.
                    </div>
                  )}
                  {progress.step === 'health' && !requiredDown && optionalDown && (
                    <div className="wizard-warning">
                      An optional service isn't running — you can continue, but some features may be limited.
                    </div>
                  )}
                  {progress.step === 'project' && submitError && (
                    <div className="wizard-warning" data-testid="wizard-project-error">
                      Couldn't create project: {submitError}
                    </div>
                  )}
                </div>
                <div className="wizard-footer">
                  <button className="wizard-back" onClick={handleBack} disabled={stepIdx === 0}>
                    ‹ Back
                  </button>
                  <div className="wizard-footer-right">
                    {progress.step === 'credentials' && (
                      <button className="wizard-link" onClick={handleNext} data-testid="wizard-skip-credentials">
                        Skip for now
                      </button>
                    )}
                    <button
                      className="btn btn-primary"
                      onClick={handleNext}
                      disabled={!valid || submitting}
                      data-testid="wizard-next"
                    >
                      {submitting ? 'Saving...' : nextLabel}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
