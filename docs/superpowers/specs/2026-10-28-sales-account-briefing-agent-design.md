# Sales Account Briefing Agent — Design

Date: 2026-10-28
Status: Proposed

## Goal

Give account executives a concise, cited pre-meeting briefing on a target
company, built from current public web sources. The agent is a Foundry
**prompt agent** with the hosted **Web Search** tool, delivered through the
existing `deploy-agent` pipeline like `research-assistant`.

Success means a seller can paste a company name (optionally with a website and
meeting context) and get a briefing they can skim in two minutes, where every
factual claim is traceable to a cited URL.

## Assumptions

- Public web data only. No CRM, email, or internal data sources.
- Single-turn usage; no conversation memory beyond what Foundry provides.
- Default model deployment, same as `research-assistant`.
- English output unless the user writes in another language.

## Non-Goals

- CRM integration (Dynamics, Salesforce) or writing back to any system.
- Contact enrichment or personal data about private individuals. Named people
  are limited to publicly listed executives and their public roles.
- Lead scoring, pricing, or competitive deal strategy.
- A hosted/containerized runtime, Toolbox, MCP servers, or Skills.
- Terraform or RBAC changes.

## User Input

Free text. The agent extracts:

| Field | Required | Example |
|-------|----------|---------|
| Company name | yes | `Contoso Ltd` |
| Website / domain | no | `contoso.com` (disambiguates same-named companies) |
| Meeting context | no | `Intro call about cloud cost optimization` |

If the company is ambiguous and no domain is given, the agent picks the most
prominent match, states the assumption in the first line, and names the
alternatives it considered.

## Output Contract

Markdown, in this order. Sections without reliable sources say
"No reliable public information found." instead of being omitted or guessed.

1. **Account Snapshot** — what the company does, HQ, size indicators
   (employees, revenue if public), industry, ownership (public/private).
2. **Recent Developments** — 3–5 items from the last 12 months (news, earnings,
   M&A, leadership changes, launches), each dated.
3. **Strategic Priorities** — stated priorities from public statements,
   annual reports, or press releases.
4. **Likely Challenges & Opportunities** — inferred from the above, explicitly
   labeled as inference, tied to the meeting context when given.
5. **Key Public Stakeholders** — publicly listed executives relevant to the
   meeting context, name and role only.
6. **Conversation Starters** — 3–5 open questions grounded in sections 2–4.
7. **Watch-outs** — controversies, layoffs, litigation, or regulatory issues
   worth knowing before the call.
8. **Sources** — the cited URLs.

Rules carried in the instructions:

- Use Web Search for every briefing; never answer from model memory alone.
- Every factual claim in sections 1–3, 5, and 7 carries an inline citation.
- Distinguish verified facts from inference; prefer official sources (company
  site, investor relations, regulatory filings) over aggregators.
- Do not invent figures, people, or sources. Do not include personal contact
  details or information about private individuals.
- State the date range of the news covered.

## Design

### Package layout

Mirror `src/agents/research_assistant/`:

```text
src/agents/sales_account_briefing/
  __init__.py       # exports AGENT_SPEC and public symbols
  instructions.py   # SALES_ACCOUNT_BRIEFING_INSTRUCTIONS, ..._SMOKE_PROMPT
  spec.py           # SALES_ACCOUNT_BRIEFING_SPEC (PromptAgentSpec)
  tools.py          # build_tools(), validate_smoke()
```

### Spec

```python
SALES_ACCOUNT_BRIEFING_SPEC = PromptAgentSpec(
    name="sales-account-briefing",
    description="Builds cited pre-meeting sales account briefings from public Web Search.",
    instructions=SALES_ACCOUNT_BRIEFING_INSTRUCTIONS,
    tools_factory=build_tools,
    smoke_prompt=SALES_ACCOUNT_BRIEFING_SMOKE_PROMPT,
    validate_smoke=validate_smoke,
    smoke_tool_choice="required",
)
```

### Tools

A single `WebSearchTool(external_web_access=True, search_context_size="medium")`.
`medium` (vs. `low` in `research-assistant`) because a briefing synthesizes
several topics per request; revisit if latency or cost is a problem.

### Smoke acceptance

- Smoke prompt: a short briefing on a large, stable public company
  (`Microsoft`, domain `microsoft.com`), limited to Account Snapshot and two
  Recent Developments, to keep the run cheap and stable.
- `validate_smoke` reuses `foundry_demo.agents.common.validators`:
  - `validate_tool_calls(evidence, "web_search_call", min_count=1)`
  - `validate_annotations(evidence, "url_citation", min_count=2)`
- The delivery package already requires a completed response with non-empty
  output text.

### Delivery wiring

- Add `foundry_demo.agents.sales_account_briefing` to
  `[tool.setuptools].packages` in `pyproject.toml`.
- Add a `kind: "prompt"` entry to `.github/deployment/config.json` with
  `source_paths: ["src/agents/sales_account_briefing/"]` and empty image fields.
- No changes to `src/delivery/` or `infra/`.

### Error handling

- Web Search failures or empty results surface as a briefing that states which
  sections lack sources; the agent never fills gaps from memory.
- Delivery failures follow the existing path: failed smoke blocks promotion and
  writes the deployment record.

## Testing

Extend `tests/test_agents.py` (and `tests/test_automation.py` where agent lists
are asserted):

- Registry discovers `sales-account-briefing` and the package is listed in
  `pyproject.toml` (covered by the existing discovery/packaging test).
- `build_tools()` returns one `WebSearchTool` with external web access.
- `validate_smoke` passes with ≥1 `web_search_call` and ≥2 `url_citation`, and
  raises `SmokeTestError` when either is missing or below threshold.
- Deployment config validation accepts the new prompt entry.

Validation commands: `uv run ruff check .` and `uv run pytest -q`.

## Documentation

- `docs/Agents.md`: add the package to the project structure and describe its
  smoke acceptance next to `research-assistant`.
- `README.md`: add the agent to the agent list.

## Acceptance Criteria

- `uv run deploy-agent --list` includes `sales-account-briefing`.
- `uv run ruff check .` and `uv run pytest -q` pass.
- A main-branch delivery deploys the agent and its Foundry smoke test passes
  (web search call + at least two URL citations).
- A manual run for a real prospect returns all eight sections with inline
  citations and no uncited figures.
