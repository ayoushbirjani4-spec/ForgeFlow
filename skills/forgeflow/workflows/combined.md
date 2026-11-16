# Combined workflow (software + content about it)

One project, one `project.json` with `workflow: "combined"`, and two ordered
phases in `project.json.phases`:

1. **developer** phase: follow `developer.md` in full (task IDs `DEV-`).
2. **creator** phase: starts only when every `DEV-` task is `completed`
   and the developer release manifest exists at
   `.forgeflow/artifacts/developer/manifest.json`.

Hand-off rules:
- The creator phase treats the developer manifest, final report and
  *validated* test output as its primary evidence. Each technical claim in the
  content cites an `EV-` file that points at one of those artifacts.
- No content may claim a feature, benchmark or behaviour the developer
  validation did not verify.
- Each phase has its own implementation approval; publishing has a third.
- `SHR-` task IDs are for work spanning both phases (for example a shared glossary).
