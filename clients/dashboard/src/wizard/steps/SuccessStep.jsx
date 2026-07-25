import { IconCheck } from '../../icons.jsx';

export default function SuccessStep({ health, models, project, onFinish }) {
  const healthyCount = health ? Object.values(health).filter((s) => s.status === 'ok').length : 0;
  const healthTotal = health ? Object.keys(health).length : 0;
  const modelsReady = [models.chat].filter(Boolean).length;

  return (
    <div className="wizard-success">
      <div className="wizard-success-icon"><IconCheck /></div>
      <div className="wizard-success-title">You're all set!</div>
      <div className="wizard-success-sub">
        {healthyCount}/{healthTotal} services healthy, {modelsReady}/1 chat model ready, and
        project "{project.name}" created.
      </div>
      <button className="btn btn-primary" onClick={onFinish} data-testid="wizard-go-to-dashboard">
        Go to Dashboard →
      </button>
    </div>
  );
}
