export default function MarketplaceTab() {
  return (
    <div className="settings-section">
      <div className="wizard-step-desc">
        Coming soon — browse and install community integrations and extensions.
      </div>
      <div className="settings-marketplace-grid" data-testid="settings-marketplace-grid">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="settings-marketplace-card">Coming soon</div>
        ))}
      </div>
    </div>
  );
}
