# Hackathon prework status

Updated October 23, 2026. This file contains no credentials.

| Prework item | Status | Evidence |
|---|---|---|
| Read supplied technical resources guide | Complete | Architecture follows Surface, Context, Toolkit, Intelligence and Action guidance. |
| Register and claim hackathon account | Complete | DataOps.live registration and Snowflake attendee credentials completed using the supplied event flow. |
| Install CoCo CLI | Complete | Official installer checksum verified; Cortex Code v1.2.18 installed at `~/.local/bin/cortex`. |
| Connect CoCo and inspect resources | Complete | CoCo onboarding authenticated `hackathon`; direct `cortex agents list` and `cortex semantic-views list` returned the Wayfinder agent and semantic view. |
| Verify Snowflake identity and Claude | Complete | `ATTENDEE_ROLE`, `DEFAULT_WH`, and Claude Sonnet 4.5 inference were verified. |
| Prepare and load synthetic data path | Complete | Runtime tables in `DEFAULT_DATABASE.WAYFINDER_POC`; passport evidence excluded. |
| Create Cortex Search and semantic view | Complete | `POLICY_SEARCH` and `TRIP_SEMANTIC` provisioned successfully. |
| Build Claude Cortex Agent | Complete | `TRAVEL_RECOVERY_AGENT` created with Analyst and Search tools. |
| Prove a managed answer | Complete | Final smoke: 41.8 seconds; `DATA_AGENT_RUN` completed with `TripSearch`, `TripAnalyst`, and `system_execute_sql`; the browser showed a Claude answer with allow-listed document sources. |
| Expose through managed MCP | Optional | The working app invokes the managed agent through Snowflake SQL. MCP is not needed for the demo path. |

Account instructions: <https://go.dataops.live/anthropic-snowflake-hackathon/instructions>

No account password, token, or connection string is committed. The local credential file and proof artifacts are ignored by Git.
