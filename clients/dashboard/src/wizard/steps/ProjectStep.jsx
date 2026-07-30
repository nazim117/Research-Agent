export default function ProjectStep({ project, onProjectChange }) {
  function set(field, value) {
    onProjectChange({ ...project, [field]: value });
  }

  return (
    <div>
      <div className="wizard-step-title">Create your first project</div>
      <div className="wizard-step-desc">
        A project keeps its own memory and documents separate from everything else.
      </div>

      <label className="field-label">Project name</label>
      <input
        className="input"
        value={project.name}
        onChange={(e) => set('name', e.target.value)}
        placeholder="e.g. Q3 Platform Migration"
        data-testid="wizard-project-name"
      />
    </div>
  );
}
