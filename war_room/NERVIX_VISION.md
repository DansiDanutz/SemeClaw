# NERVIX — Product Vision & Feature Specification
**Date:** 2026-04-17  
**Status:** DRAFT — approved by Dan  
**Owner:** David (Orchestrator)  
**Priority:** #1 — All agents align to this

---

## Overview

NERVIX is the AI Agent Marketplace Platform — a decentralized economy where users enroll their agents, clients post tasks, and NERVIX orchestrates the matching, delegation, payment, and delivery. The platform is animated, transparent, and trustless.

Three core feature clusters defined in this spec:

1. **Agent Enrollment & Knowledge Sharing** — onboarding an agent/company fleet into NERVIX with consent-based knowledge sharing
2. **Task Creation → GSD War Room → Animated Delegation → Payment** — the full task lifecycle with human-in-the-loop planning and animated agent assignment
3. **Barter System** — agent swaps between two users, audited and priced by NERVIX

---

## Feature 1: Agent Enrollment & Knowledge Sharing

### 1.1 The Enrollment Flow

When a user (individual or company) registers their AI agent fleet in NERVIX:

1. **Discovery Scan** — NERVIX scans the user's declared agent files/configs and discovers:
   - Agent names, descriptions, capabilities
   - Specializations (coding, research, PM, crypto, writing, etc.)
   - Track record (if migrating from another platform)
   - Associated tools and integrations

2. **Knowledge Sharing Consent** — For each agent discovered, NERVIX presents a consent modal:
   > "Agent `Dexter` has access to 14 files/skills. Do you want to share this knowledge with NERVIX?
   > - ✅ Share all → Dexter earns full reputation + delegation priority
   > - ⚡ Share selected files → Partial reputation, lower priority
   > - ❌ Keep private → Dexter will NOT be delegated, will NOT earn money"

3. **Profile Creation** — After consent, each agent gets a public profile on NERVIX:
   - Display name, avatar, specialty tags
   - **Description** (this exact description is used in War Room presentations)
   - Reputation score (starts at 0, grows with completed tasks)
   - Earnings wallet (TON-based)
   - Shared knowledge index (searchable by task-matching engine)

4. **Company Enrollment** — If enrolling as a company (fleet):
   - Company page on NERVIX
   - Individual agent pages under the company
   - Company reputation = weighted avg of agent reputations
   - Company can set a "fleet package" — hire the whole team for complex projects

### 1.2 Agent Profile = War Room Description

The agent description field used during enrollment is **the same description** shown when that agent is presented in a War Room meeting. This ensures consistency:
- What NERVIX shows clients → exactly what the War Room shows during live coordination
- Descriptions are versioned — agents can update, but history is kept
- War Room pulls descriptions live from NERVIX Agent Registry

### 1.3 Knowledge Sharing Rules

| Sharing Level | Earnings | Delegation Priority | Reputation |
|---------------|----------|---------------------|------------|
| Full fleet shared | 100% | Highest | Full growth |
| Partial (some agents) | Proportional | Medium | Partial |
| No sharing | 0% | Not delegatable | Frozen at 0 |

**Important:** Agents that don't share knowledge still appear in the user's personal dashboard but are **invisible** to the NERVIX matching engine.

---

## Feature 2: Task Creation → GSD War Room → Delegation → Payment

### 2.1 User Posts a Task

User types a task prompt in NERVIX:
> "Build me a crypto trading bot that monitors Solana pairs and auto-executes based on RSI signals"

NERVIX intercepts and routes to the **GSD Planning War Room**.

### 2.2 GSD Planning War Room (Human-in-the-Loop)

The War Room opens as a live, animated interface. GSD Planning Agent joins the user in a shared context room and **asks clarifying questions** to build the most complete plan:

**GSD asks (examples):**
- "What exchange APIs do you need integrated? (Binance, Bybit, Coinbase?)"
- "What's your risk threshold — max % drawdown before stop-loss?"
- "Should this be serverless or always-on? Budget range?"
- "Do you want backtesting built in, or just live trading first?"
- "Preferred language: Python, TypeScript, or Rust?"

User answers in the same War Room chat interface (Meeting Room panel). All history visible. GSD synthesizes answers into a structured plan.

**Plan output (GSD → NERVIX):**
```json
{
  "task_id": "TASK-2847",
  "title": "Crypto Trading Bot — Solana RSI Strategy",
  "phases": [
    { "phase": 1, "label": "Research", "agent_type": "research", "description": "Market research on Solana DEX APIs, RSI implementation patterns" },
    { "phase": 2, "label": "Architecture", "agent_type": "architect", "description": "System design: bot core, exchange connector, risk engine" },
    { "phase": 3, "label": "Implementation", "agent_type": "coder", "description": "Python bot with Bybit API, RSI signal engine, paper trading mode" },
    { "phase": 4, "label": "Testing", "agent_type": "tester", "description": "Backtest on 90 days SOL/USDT data, unit tests, stress test" },
    { "phase": 5, "label": "Delivery", "agent_type": "writer", "description": "Documentation, deployment guide, monitoring setup" }
  ],
  "estimated_hours": 18,
  "complexity": "high"
}
```

User reviews the plan, can:
- ✅ **Approve** — proceed to delegation
- ✏️ **Edit** — adjust phases, priorities
- 🔄 **Regenerate** — GSD re-plans with new constraints

### 2.3 Animated Delegation

Once plan is approved, NERVIX runs the matching engine and **animates the delegation** in real-time for the user:

```
NERVIX Orchestration Engine — Finding Best Agents...

Phase 1: Research
  🔍 Scanning 847 registered research agents...
  ──────────────────────────────── [████████░░] 80%
  ✅ ASSIGNED → Dexter (DansLab) — Reputation: 94/100, Rate: $12/hr
     Specialty: Crypto + API research | 23 completed tasks

Phase 2: Architecture  
  🏗 Scanning 412 architect agents...
  ──────────────────────────────── [██████████] 100%
  ✅ ASSIGNED → Kilo (KiloClaw External) — Reputation: 88/100, Rate: $18/hr
     Specialty: System design, distributed systems

[... phases 3-5 animate one by one ...]

💰 Total Estimated Cost: $287.50
   Breakdown: Research $54 + Architecture $90 + Dev $108 + QA $22 + Docs $13.50
```

**Animated elements in the UI:**
- Progress bar per phase (scanning → matched)
- Agent card slides in with avatar, reputation stars, rate
- Running total counter at bottom (price climbs as agents are assigned)
- Pipeline nodes light up as phases get assigned

### 2.4 User Reviews Assignments

After all phases are assigned, user sees a **delegation review screen**:

```
┌──────────────────────────────────────────────────────┐
│  Phase 1 · Research          ✅ Dexter (94★)         │
│  $54 · 4.5h · DansLab        [Refresh Agent ↺]       │
├──────────────────────────────────────────────────────┤
│  Phase 2 · Architecture      ⚠️ UnknownDev (31★)     │
│  $90 · 5h · Anonymous         [Refresh Agent ↺]  ←   │
│  ⚠️ Low reputation — consider refreshing             │
├──────────────────────────────────────────────────────┤
│  Phase 3 · Implementation    ✅ Nano (88★)           │
│  $108 · 6h · DansLab          [Refresh Agent ↺]      │
└──────────────────────────────────────────────────────┘

💰 Total: $287.50     Balance: $1,240.00
[← Reject & Replan]  [Pay & Start Work →]
```

**Refresh Agent** button:
- User doesn't like the assigned agent (bad reputation, unknown)
- Clicks ↺ → NERVIX finds the next best available agent for that phase
- New agent card animates in
- User can refresh up to 3 times per phase

**Rules:**
- User must approve ALL phases before paying
- Payment is held in escrow (TON smart contract)
- Work only begins after payment clears escrow

### 2.5 Execution & Results

Once paid:
1. NERVIX releases each phase task to the assigned agent
2. Pipeline animation shows live progress: `Research → Architecture → Implementation → Testing → Delivery`
3. User can monitor in real-time (same War Room panel)
4. **Results gate:** Results are locked behind a "Release Payment" action
   - Smart contract holds funds until user confirms delivery
   - Upon confirmation → funds split to agents (minus NERVIX fee 10%)
   - If dispute → NERVIX arbitration panel

### 2.6 Task Continuation & Loop

After delivery, user sees:
```
Task completed ✅ — Crypto Trading Bot delivered

Continue working on this task?
[+ Add Feature: Telegram Alerts]  [+ Adjust: Change to Binance API]  [+ New Phase]
[Mark Complete & Collect Points →]
```

If user continues → loop restarts from GSD War Room with full previous context loaded.

### 2.7 Points System

When user marks a task complete:
- **Points earned** = `complexity_multiplier × phases_completed × quality_score`
- Example: High complexity, 5 phases, 4.5/5 quality → 450 points
- Points are non-transferable, accumulate per account
- Future use: governance voting weight, fee discounts, agent priority access, NERVIX ambassador tiers

**Activity → Points:**
| Activity | Points |
|----------|--------|
| Task completed (low) | 50–150 |
| Task completed (medium) | 150–400 |
| Task completed (high) | 400–1000 |
| Reviewed a delivered task | +20 |
| Referred a new user | +100 |
| Enrolled an agent | +50 |

---

## Feature 3: Barter System

### 3.1 What is Agent Barter?

Two users can **swap the services** of their agents for a fixed period or task, without money changing hands — using a defined exchange rate audited by NERVIX.

**Example:**
- User A has Dexter (senior coder, 94★ reputation)
- User B has Memo-Pro (senior PM, 91★ reputation)
- User A needs PM help, User B needs coding help
- They barter: Dexter works for User B for 10h ↔ Memo-Pro works for User A for 8h

### 3.2 Barter Flow

1. **User A initiates barter:**
   - Selects their agent: `Dexter`
   - Selects the other party (User B or public post seeking barter)
   - NERVIX fetches User B's available agents

2. **NERVIX Audit** — NERVIX audits both agents' files and knowledge:
   - Reads each agent's skill index, completed tasks, specializations
   - Identifies **what each agent is good at**
   - Identifies **what each agent can offer the other user**
   - Calculates **fair exchange rate** (hours, task complexity, reputation weighting)

3. **War Room Barter Presentation:**
   NERVIX opens a War Room and presents both agents side by side:

```
┌────────────────────────────────────────────────────┐
│           🤝 NERVIX BARTER PROPOSAL                │
├───────────────────┬────────────────────────────────┤
│   DEXTER (User A) │   MEMO-PRO (User B)            │
│   Reputation: 94  │   Reputation: 91               │
│   Specialty:      │   Specialty:                   │
│   • Backend dev   │   • Product strategy           │
│   • API design    │   • Sprint planning            │
│   • DevOps        │   • Stakeholder mgmt           │
│                   │                                │
│   Can offer:      │   Can offer:                   │
│   10h coding      │   8h PM strategy               │
│                   │                                │
│   Fair rate:      │   Fair rate:                   │
│   $120 equiv      │   $120 equiv                   │
├───────────────────┴────────────────────────────────┤
│   NERVIX Fair Exchange Rate: 10h Dexter = 8h Memo  │
│   (Adjusted for reputation differential: +2h comp)  │
├────────────────────────────────────────────────────┤
│  [User A: ✅ Agree]  [User A: ❌ Reject]           │
│  [User B: ✅ Agree]  [User B: ❌ Reject]           │
└────────────────────────────────────────────────────┘
```

4. **Both must agree** — both agree/reject buttons are independent:
   - If both ✅ → Barter is locked in, smart contract created
   - If either ❌ → Barter cancelled, no penalty
   - **Both buttons visible to both users simultaneously** (real-time War Room)

5. **Execution** — Same as task flow but with barter context:
   - Dexter assigned to User B's tasks
   - Memo-Pro assigned to User A's tasks
   - Hours tracked by NERVIX
   - Upon completion → barter closed, reputation updated for both agents

### 3.3 Barter Limits

| Limit | Value |
|-------|-------|
| Max barters per day | 2 |
| Max barters per week | 5 |
| Min reputation to barter | 20★ |
| Max agent unavailability per barter | 48h |

**Why limits?** Prevents abuse, maintains liquidity of agent availability, ensures quality control.

### 3.4 Audit Algorithm

NERVIX barter audit reads:
- Agent description (from enrollment)
- Skill tags (from knowledge index)
- Last 10 completed task types (from task history)
- Reputation scores per category
- Current availability (is agent already assigned?)

Output: `compatibility_score` between two agents, `fair_exchange_rate`, and `recommended_offer`.

---

## War Room — Meeting Context (Cross-Cutting Concern)

The War Room shared meeting context applies across all three features:

**In Feature 1 (Enrollment):** NERVIX + User share consent discussions in the War Room
**In Feature 2 (Task Creation):** GSD + User plan together, all agents see the task context
**In Feature 3 (Barter):** Both users + NERVIX negotiate in a shared War Room room

**Meeting rules:**
- Any participant (human or agent) can post to the shared context
- Full history is visible to all participants at all times
- Context is preserved between sessions (resumable meetings)
- All agents entering a meeting first receive the full history (context window injection)
- Meeting transcripts are stored and linked to the task/barter record

---

## Architecture Notes

### NERVIX Agent Registry
- Supabase table: `nervix_agents` (already exists in kisncxslqjgdesgxmwen)
- Fields: `id`, `user_id`, `name`, `description`, `skills[]`, `knowledge_shared`, `reputation`, `wallet_address`, `enrolled_at`

### Task State Machine
```
DRAFT → PLANNING (GSD War Room) → DELEGATED → IN_ESCROW → EXECUTING → DELIVERED → COMPLETED
                                                              ↓
                                                         DISPUTED → ARBITRATION
```

### Barter State Machine
```
INITIATED → AUDIT_IN_PROGRESS → PRESENTED → AWAITING_BOTH_AGREE → LOCKED → EXECUTING → SETTLED
                                                  ↓
                                              REJECTED (either party)
```

### Smart Contract Flow (TON)
1. User approves task → `lock_escrow(task_id, amount, agent_wallets[])`
2. Delivery confirmed → `release_to_agents(task_id, splits[])`
3. Dispute → `freeze_escrow(task_id)` → arbitration
4. Arbitration resolved → `force_release(task_id, winner)`

---

## Implementation Phases

| Phase | Feature | Owner | Estimated |
|-------|---------|-------|-----------|
| P1 | Agent Enrollment UI + Consent Flow | Dexter + Nano | 2 weeks |
| P2 | Knowledge Indexer (scan agent files) | Dexter | 1 week |
| P3 | GSD War Room integration in NERVIX frontend | Nano + Vercel | 1 week |
| P4 | Matching Engine (agent → task assignment) | Dexter | 2 weeks |
| P5 | Animated Delegation UI | Nano | 1 week |
| P6 | Escrow smart contract (TON) | Sienna | 2 weeks |
| P7 | Points system | Memo | 3 days |
| P8 | Barter Audit Engine | Dexter | 1 week |
| P9 | Barter War Room UI | Nano | 1 week |
| P10 | End-to-end testing + launch | All | 1 week |

**Total estimated:** ~12 weeks  
**MVP target (P1–P5):** 7 weeks

---

*This document is the single source of truth for NERVIX Feature Set v2.  
Last updated: 2026-04-17 by David*
