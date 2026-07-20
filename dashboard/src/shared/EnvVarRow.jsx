import { IconLoader } from '../icons.jsx';

// Secret fields never receive a real value from the server (write-only
// contract — see api.listEnvVars/updateEnvVar) so there's nothing to
// prefill or reveal: the input starts blank and only a "configured" status
// + masked hint (e.g. "…ab12") shows what's already set. Shared by
// Settings' Advanced tab and the wizard's Credentials step — same real
// save path, one implementation.
export default function EnvVarRow({ envVar, draft, onChange, onSave, saving }) {
  // Secrets always start blank (nothing to prefill — write-only contract);
  // non-secret vars are prefilled with their real value and editable in place.
  const value = draft ?? (envVar.secret ? '' : (envVar.hint ?? ''));
  const dirty = envVar.secret ? value !== '' : value !== (envVar.hint ?? '');

  return (
    <div className="settings-envvar-row" data-testid={`settings-envvar-${envVar.key}`}>
      <span className="settings-envvar-key">{envVar.key}</span>
      <div className="settings-envvar-value">
        <input
          className="input"
          type={envVar.secret ? 'password' : 'text'}
          value={value}
          onChange={(e) => onChange(envVar.key, e.target.value)}
          placeholder={envVar.secret ? 'Leave blank to keep current value' : ''}
        />
        {envVar.secret && (
          <div className="wizard-status-detail mt-8">
            {envVar.configured ? `Configured — ${envVar.hint}` : 'Not set'}
          </div>
        )}
      </div>
      <div className="settings-envvar-actions">
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => onSave(envVar.key)}
          disabled={!dirty || saving}
        >
          {saving ? <IconLoader className="spin" /> : 'Save'}
        </button>
      </div>
    </div>
  );
}
