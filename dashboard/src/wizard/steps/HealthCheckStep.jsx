import { IconLoader, IconRefresh } from '../../icons.jsx';
import { useHealthChecks } from '../../shared/useHealthChecks.js';
import ServiceStatusRow from '../../shared/ServiceStatusRow.jsx';

export default function HealthCheckStep({ health, onHealthChange }) {
  const { checking, checkError, fixState, runCheck, handleFix } = useHealthChecks(health, onHealthChange);

  return (
    <div>
      <div className="wizard-step-title">Health check</div>
      <div className="wizard-step-desc">
        We check that the services Research Agent depends on are reachable on this machine.
      </div>

      {!health && checking && (
        <div className="row-gap-sm">
          <IconLoader className="spin" /> Checking services...
        </div>
      )}

      {!health && !checking && checkError && (
        <div>
          <div className="wizard-warning" data-testid="wizard-health-error">
            Couldn't check service status: {checkError}
          </div>
          <button className="btn btn-secondary btn-sm mt-8" onClick={runCheck} data-testid="wizard-health-retry">
            <IconRefresh /> Retry
          </button>
        </div>
      )}

      {health && Object.entries(health).map(([service, info]) => (
        <ServiceStatusRow
          key={service}
          service={service}
          info={info}
          fix={fixState[service]}
          onFix={() => handleFix(service)}
        />
      ))}

      {health && (
        <button className="btn btn-secondary btn-sm mt-8" onClick={runCheck} disabled={checking}>
          {checking ? <><IconLoader className="spin" /> Rechecking...</> : <><IconRefresh /> Recheck all</>}
        </button>
      )}
    </div>
  );
}
