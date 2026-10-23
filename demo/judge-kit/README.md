# Judge demo kit

This folder contains everything needed for a repeatable Wayfinder presentation.

- `index.html` — presenter console with the timed pitch and click-by-click live demo.
- `PITCH.md` — the three-minute spoken pitch.
- `LIVE-DEMO.md` — the live demo runbook, expected states, and fallback paths.
- `inputs/` — the five sanitized files to select together in the Wayfinder upload dialog.

Start Wayfinder from the repository root:

```sh
./scripts/run.sh
```

Then open both:

- App: <http://127.0.0.1:8765>
- Presenter console: `demo/judge-kit/index.html`

The five fixture files contain fictional booking references and no credentials, real traveler identity, or usable locator. The Chile disruption is a labeled provider-style replay. The London replacement inventory and confirmation are simulated; no real booking or payment occurs.
