# API design archive

[API README](../README.md) · [WorkReady project home](https://github.com/michael-borck/workready-deploy) · [Current guides](https://github.com/michael-borck/workready-deploy/blob/main/docs/README.md)

The files below preserve design discussions and implementation plans. Their checkboxes, status lines, commands and endpoint examples describe the period when they were written. They are not a current contract or release checklist.

| Record | How to read it now |
|---|---|
| [Previous system map](archive/system-map-before-docs-consolidation.md) | Mixed-era overview archived during September consolidation; replaced by the umbrella architecture/configuration/operations guides |
| [Authentication migration](AUTH-MIGRATION.md) | Original email-to-code proposal; 0.3.0 uses opaque hashed sessions, not the proposed JWT scheme. Existing production data must be preserved |
| [Stages 4, 5 and 6](../STAGES-4-5-6.md) | April teaching design; bonus tasks and a general curriculum-duration generator did not ship |
| [Team communications design](superpowers/specs/2026-04-14-workready-team-communications-design.md) | April design for directory, chat, context and message review |
| [Team communications implementation plan](superpowers/plans/2026-04-14-team-communications-layer3.md) | Historical implementation steps, not instructions to reapply migrations or old code |
| [Team communications future work](superpowers/specs/2026-04-14-workready-team-communications-future-work.md) | Ideas deferred at that time; not a committed schedule or current backlog |
| [Hiring-desk implementation plan](superpowers/plans/2026-04-14-hiring-desk-anythingllm.md) | Original AnythingLLM setup/embed approach; check current deploy scripts and page exclusions |
| [Portal sidebar design](superpowers/specs/2026-04-16-portal-sidebar-teams-ux-design.md) | April UI rationale |
| [Portal sidebar implementation plan](superpowers/plans/2026-04-16-portal-sidebar-teams-ux.md) | Historical frontend implementation steps |

The [September audit](https://github.com/michael-borck/workready-deploy/blob/main/AUDIT-2026-09-14.md) records pre-fix findings. The [0.3.0 release record](https://github.com/michael-borck/workready-deploy/blob/main/PRIVACY-RELEASE.md) records the subsequent rollout. Current accepted decisions are documented in the [ADRs](https://github.com/michael-borck/workready-deploy/blob/main/docs/adr/README.md).
