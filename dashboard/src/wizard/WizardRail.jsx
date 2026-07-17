const STEP_LABELS = {
  welcome: 'Welcome',
  health: 'Health Check',
  models: 'Models',
  credentials: 'Credentials',
  project: 'Project',
};

export default function WizardRail({ steps, currentStep, completedSteps }) {
  const doneCount = steps.filter((s) => completedSteps.includes(s)).length;

  return (
    <nav className="wizard-rail" aria-label="Setup steps">
      <ul className="wizard-rail-list">
        {steps.map((step) => {
          const done = completedSteps.includes(step);
          const current = step === currentStep;
          const cls = `wizard-rail-step ${done ? 'done' : ''} ${current ? 'current' : ''}`.trim();
          return (
            <li key={step} className={cls} data-testid={`wizard-rail-${step}`}>
              <span className="wizard-rail-dot" />
              {STEP_LABELS[step]}
            </li>
          );
        })}
      </ul>
      <div className="wizard-rail-progress">{doneCount} / {steps.length} done</div>
    </nav>
  );
}
