# AgentX Pack for speedRouter

This repository is upgraded with AgentX capabilities.

## Included
- `agents.json`: merged core + repo-specific speedRouter agents
- `access_profiles.json`: safe/balanced/power profiles
- `agency_import.json`: agents imported from `.github/agents/` (GitHub Copilot custom agent definitions)

## Suggested commands
```bash
agentx list
agentx find speedrouter
agentx check speedrouter-implementation-pilot --profile balanced
agentx check speedrouter-orchestrator --profile power
agentx check speedrouter-security-auditor --profile safe
```

## Re-syncing .github/agents into the registry

After adding or editing files in `.github/agents/`, run:

```bash
agentx import-agency .github/agents --output .agentx/agency_import.json
```

To merge imported agents into the main registry:

```bash
agentx import-agency .github/agents --merge --merge-target agent_tools/data/agents.json
```

## Extra learning / agent-tools

Additional agent definitions and tools are available at:
https://github.com/Unwrenchable/agent-tools.git

To import from that repo once cloned locally:

```bash
agentx import-agency /path/to/agent-tools --merge
```
