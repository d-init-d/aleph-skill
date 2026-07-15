# Third-party notices

## Bundled component: D Research (`components/d-research`)

- **Package:** d-research-skill-tools
- **Upstream:** https://github.com/d-init-d/d-research-skill
- **Pinned identity:** recorded in `component-lock.json` (`source_tag`, annotated tag object, commit, Git tree, reproducible `git archive` digest, exact snapshot recipe, per-file digests, and component-tree digest)
- **License:** Creative Commons Attribution-NonCommercial 4.0 International (CC-BY-NC-4.0)
- **Full license text:** `components/d-research/LICENSE`

D Research is vendored as a content-locked, tamper-evident internal component of Aleph. It is not a second installable skill. Hosts must load only the Aleph root entrypoint (`SKILL.md`). Nested `components/d-research/SKILL.md` is an internal resource for agents that already run under Aleph.

Optional runtime dependencies of D Research (Node.js, Playwright, Chromium, Python extras) are **not** bundled and are **not** auto-installed. Missing capabilities must produce structured blockers rather than fabricated evidence.

## Aleph Skill license

Aleph itself is also distributed under CC-BY-NC-4.0; see `LICENSE`.
