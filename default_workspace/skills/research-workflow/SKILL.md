---
name: research-workflow
description: Research workflow for Dan's Lab - save notes, search past research, analyze competitors, manage architecture docs
trigger: research, save research, search research, competitors, architecture, papers, market research
---

# Research Workflow for Dan's Lab

## Overview
This skill manages Dan's Lab research pipeline. All research is stored in the `research/` folder in the workspace.

## Folder Structure
```
research/
  notes/          - Daily research notes and ideas
  competitors/    - Competitor analysis docs
  architecture/   - NERVIX architecture decisions
  papers/         - AI paper summaries
  market/         - Market research and trends
```

## Workflows

### Saving Research Notes
When asked to save research, follow this template and save to `research/notes/{slug}.md`:

```markdown
# [Research Topic]

Date: YYYY-MM-DD
Tags: #tag1 #tag2

## Summary
One-line summary

## Key Findings
- Finding 1
- Finding 2

## Implications for NERVIX
- Implication 1

## Action Items
- [ ] Action 1

## Sources
- [Source Title](URL)
```

### Competitor Analysis
When asked about competitors, check `research/competitors/` first.
Save new analysis to `research/competitors/{company-name}.md` with:
- Company overview
- Product comparison to NERVIX
- Strengths/weaknesses
- Key takeaways for Dan's Lab

### Architecture Decisions
When asked about architecture, check `research/architecture/` first.
Save decisions to `research/architecture/{topic}.md` using ADR format:
- Context: What situation prompted this decision
- Decision: What was decided
- Consequences: What results from this decision

### Paper Summaries
When asked to summarize a paper, save to `research/papers/{paper-slug}.md`:
- Title, authors, date
- Key contributions
- Relevance to NERVIX
- Notable techniques or findings

### Market Research
When asked about market trends, check `research/market/` first.
Save findings to `research/market/{topic}.md`:
- Market overview
- Key players
- Trends relevant to NERVIX
- Opportunities identified

## Search Workflow
When searching past research:
1. First check the relevant subfolder (notes/, competitors/, etc.)
2. Use file search across all research/ folders
3. Summarize what was found and highlight relevance

## Web Research Workflow
When asked to research something new:
1. Use web_search to find current information
2. Use web_read to extract full content from top results
3. Summarize findings using the appropriate template
4. Save to the correct research/ subfolder
5. Report findings and where they were saved