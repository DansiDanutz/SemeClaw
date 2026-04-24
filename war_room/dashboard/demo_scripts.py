"""
Scripted agent conversations for the /agents demo view.

A scenario is a mini-play with:
  - a meeting CARD (shown first, before anything moves):
      title, agenda, attendees, kickoff line
  - an INTRO turn: the orchestrator frames the problem and introduces the
    roster ("We are here to solve…")
  - a sequence of DIALOG turns: short, reactive lines where each agent
    quotes or builds on the previous one — not a relay of monologues
  - a HANDOFF turn: a brief bridge line from the previous speaker to the
    next
  - an OUTRO turn: the orchestrator closes the meeting

Every speaking block carries a `voice` field — an edge-tts neural voice id.
We mix accents (American / British / Australian) so the agents sound like
clearly different people, not four siblings of the same robot.

Per-agent `why_assigned` strings are also exposed on the scenario so the UI
can speak a short bio when the user clicks an agent card:
  "Hi, I'm Dexter, your lead researcher. I'm here because…"
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Voice catalog — mix accents for maximum distinctiveness
# ---------------------------------------------------------------------------
VOICES = {
    "narrator": "en-US-SteffanNeural",  # warm, authoritative American male
    "research": "en-AU-WilliamNeural",  # Australian male — curious, analytical
    "strategist": "en-GB-SoniaNeural",  # British female — confident, decisive
    "writer": "en-US-AndrewNeural",  # American male — clear, articulate
    "architect": "en-GB-RyanNeural",  # British male — technical, grounded
}

# Per-voice prosody nudge (edge-tts supports rate / pitch offsets)
VOICE_PROSODY = {
    "narrator": {"rate": "-4%", "pitch": "+0Hz"},
    "research": {"rate": "+3%", "pitch": "+0Hz"},
    "strategist": {"rate": "+0%", "pitch": "+0Hz"},
    "writer": {"rate": "-2%", "pitch": "+0Hz"},
    "architect": {"rate": "+1%", "pitch": "-1Hz"},
}

DISPLAY_NAMES = {
    "narrator": "Orchestrator",
    "research": "Dexter",
    "strategist": "Memo",
    "writer": "Hermes",
    "architect": "David",
}

ROLE_LABELS = {
    "narrator": "Orchestrator",
    "research": "Lead Researcher",
    "strategist": "Strategist",
    "writer": "Writer & Ticket-Drafter",
    "architect": "Systems Architect",
}

# Emoji glyph per agent, used in meeting cards
AGENT_EMOJI = {
    "narrator": "🎙",
    "research": "🔬",
    "strategist": "🎯",
    "writer": "✍️",
    "architect": "🏗",
}


# ---------------------------------------------------------------------------
# Helpers to build the narrator's intro / outro from scenario metadata
# ---------------------------------------------------------------------------


def _intro_script(problem: str, agents: list[str]) -> str:
    roster = []
    for a in agents:
        name = DISPLAY_NAMES.get(a, a.capitalize())
        role = ROLE_LABELS.get(a, a)
        roster.append(f"{name}, our {role.lower()}")
    if len(roster) > 1:
        roster_text = ", ".join(roster[:-1]) + ", and " + roster[-1]
    else:
        roster_text = roster[0]

    return (
        f"Welcome to the War Room. We are here to solve one problem. "
        f"{problem} "
        f"To succeed, I have delegated and orchestrated the following agents "
        f"to coordinate the workflow. Joining us today: {roster_text}. "
        f"Each one will take the floor, build on the last contribution, and hand off cleanly. "
        f"The table is open. Let's begin."
    )


def _outro_script(deliverable: str) -> str:
    return (
        f"Good work, team. The room has reached alignment and we are closing "
        f"with {deliverable} Minutes are logged, the ticket is queued, and the "
        f"War Room is closed."
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
# Dialog design notes:
#   * Every agent speaks more than once so the conversation has give-and-take
#   * Each follow-up line EXPLICITLY quotes or reacts to a prior line
#     ("Picking up Dexter's point about…", "I'd push back on the flat tier",
#      "Memo, before we write that up, one concern…")
#   * Short turns (one or two ideas) keep the rhythm conversational
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    # =======================================================================
    # Welcome / out-of-box demo (uses only built-in agents)
    # =======================================================================
    "welcome": {
        "title": "Welcome to the War Room",
        "agenda": "A quick tour of how agent meetings work — no setup required.",
        "kickoff": "This meeting runs entirely inside SemeClaw. No external agents needed.",
        "task": "Learn how the War Room orchestrates multi-agent discussions.",
        "agents": ["research", "strategist", "writer"],
        "problem": (
            "New users need to see what a War Room agent meeting looks like without installing anything external."
        ),
        "deliverable": ("a three-minute demo that shows intro, dialog, handoff, and outro."),
        "why_assigned": {
            "research": (
                "Hi, I'm Dexter. I'm here to set the scene and explain why multi-agent "
                "meetings matter for complex decisions."
            ),
            "strategist": (
                "Hi, I'm Memo. I'm here to show how a strategist reframes research into actionable positions."
            ),
            "writer": ("Hi, I'm Hermes. I'm here to demonstrate how decisions turn into clean, shippable tickets."),
        },
        "turns": [
            {
                "kind": "intro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 200,
                "message": None,
                "reasoning": "Framing the demo and the roster.",
            },
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 500,
                "reasoning": "Setting the scene: why multi-agent meetings?",
                "message": (
                    "Dexter here. When a decision touches research, architecture, strategy, and documentation, "
                    "one brain can't hold all the constraints at once. A War Room meeting splits the work across "
                    "specialists who debate, push back, and hand off cleanly. The result is faster alignment and "
                    "fewer blind spots. Memo, does that match how you use the room?"
                ),
            },
            {
                "kind": "speak",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 400,
                "reasoning": "Reframes research into a wedge and a decision.",
                "message": (
                    "It does, Dexter — and I'd add one thing. The real value isn't just the split. "
                    "It's the pushback. When I have to defend a position to a researcher who actually read the docs, "
                    "the strategy gets sharper. Three principles we follow in this room. One: every agent speaks twice so "
                    "the conversation has give-and-take. Two: handoffs are explicit, not implied. Three: the writer "
                    "closes with a ticket, not a summary. Hermes, ready to show what that looks like?"
                ),
            },
            {
                "kind": "handoff",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 200,
                "message": "Over to you, Hermes.",
            },
            {
                "kind": "speak",
                "agent": "writer",
                "voice": VOICES["writer"],
                "delay_ms": 400,
                "reasoning": "Demonstrates the ticket output format.",
                "message": (
                    "On it. Title: War Room demo — multi-agent meeting template. "
                    "Acceptance criteria. One: the demo runs with zero external dependencies. "
                    "Two: three agents speak in a realistic arc — intro, debate, handoff, outro. "
                    "Three: the output is a ticket any team can clone and adapt. "
                    "This is exactly how we close every real meeting in this room."
                ),
            },
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 350,
                "reasoning": "Second turn — confirms and closes the loop.",
                "message": (
                    "Confirmed. I'll own the demo template and refresh it weekly. "
                    "The welcome scenario is now part of the default roster — "
                    "anyone can launch the War Room and see this meeting in under ten seconds."
                ),
            },
            {
                "kind": "outro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 500,
                "message": None,
                "reasoning": "Wrapping the demo.",
            },
        ],
    },
    # =======================================================================
    # NERVIX competitor research
    # =======================================================================
    "nervix-marketplaces": {
        "title": "NERVIX Positioning",
        "agenda": "Research open-source agent marketplaces and pin down a defensible position for NERVIX.",
        "kickoff": "Launch is six weeks out. We leave this room with a one-page positioning note and a ticket in Paperclip.",
        "task": "Research open-source AI agent marketplaces and draft a NERVIX positioning note.",
        "agents": ["research", "strategist", "writer"],
        "problem": (
            "NERVIX needs a sharp, defensible position in the open-source agent marketplace landscape before launch."
        ),
        "deliverable": ("a positioning note and a Paperclip ticket for the landing page rewrite."),
        "why_assigned": {
            "research": (
                "Hi, I'm Dexter, your lead researcher. I'm on this meeting because somebody has to "
                "actually read the competitor docs and pull the numbers. I'll bring the evidence, you decide what to do with it."
            ),
            "strategist": (
                "Hello, I'm Memo. I'm here to turn Dexter's research into a position we can defend. "
                "Specifically: what's our wedge, what do we say no to, and where is the moat."
            ),
            "writer": (
                "Hi, I'm Hermes. I'm here to turn the decisions in this room into a ticket the engineering team can ship. "
                "Clean title, tight acceptance criteria, no ambiguity."
            ),
        },
        "turns": [
            # ---- narrator opens ------------------------------------------
            {
                "kind": "intro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 200,
                "message": None,
                "reasoning": "Framing the problem and the roster.",
            },
            # ---- research opens the discussion ---------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 500,
                "reasoning": "Setting the landscape. Three players, one gap.",
                "message": (
                    "Thanks. Dexter here. Let me set the landscape. I looked at the three marketplaces "
                    "people actually use this quarter: Smithery, LangSmith Hub, and LlamaHub. "
                    "Smithery is the cleanest — one-line install for MCP servers, but it's a directory, not a runtime. "
                    "LangSmith Hub carries LangChain's weight, but the primitive is a prompt, not an agent. "
                    "LlamaHub ships retrieval loaders — no runtime, no billing. "
                    "So here's the gap I keep circling back to: nobody is hosting runnable agents with payments attached. Memo, does that match your read?"
                ),
            },
            # ---- strategist reacts and pushes ---------------------------
            {
                "kind": "speak",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 400,
                "reasoning": "Reacts to Dexter's gap; reframes it as a wedge.",
                "message": (
                    "It does, Dexter — and I'd go a step further. The word 'runnable' is the wedge. "
                    "If someone has to clone a repo, set up a venv, and wire secrets, that is a prompt library, not an app store. "
                    "I'd position NERVIX explicitly against that. Three pillars: one, zero-to-running in under sixty seconds. "
                    "Two, a real runtime that handles memory and retries so creators don't reinvent it. "
                    "Three — and this is the piece none of the others have — a revenue split for the creator."
                ),
            },
            # ---- research challenges ------------------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 350,
                "reasoning": "Pushes back on the sixty-second claim.",
                "message": (
                    "One check on the sixty-second claim. Smithery is already close to that for MCP. "
                    "If we lead with speed alone, we look derivative. The payout line is the one nobody else can copy in a week."
                ),
            },
            # ---- strategist concedes and refines ------------------------
            {
                "kind": "speak",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 300,
                "reasoning": "Concedes, reorders the pillars.",
                "message": (
                    "Fair. Then we flip the order. Payouts lead. Runtime second. Install speed third as a proof point, not the headline. "
                    "Hermes, can you write that up so the landing page matches the order?"
                ),
            },
            # ---- handoff ------------------------------------------------
            {
                "kind": "handoff",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 200,
                "message": "Over to you, Hermes.",
            },
            # ---- writer drafts and asks a clarifying question -----------
            {
                "kind": "speak",
                "agent": "writer",
                "voice": VOICES["writer"],
                "delay_ms": 400,
                "reasoning": "Drafting the ticket, confirming scope.",
                "message": (
                    "On it. Title of the ticket: NERVIX positioning — the runnable agent marketplace, creator-first. "
                    "Three acceptance criteria in Memo's new order. One: the hero headline names creator earnings. "
                    "Two: a feature comparison table against Smithery, LangSmith Hub, and LlamaHub sits above the fold. "
                    "Three: a public creator-earnings page ships before launch day. "
                    "Quick check — Dexter, can you own the comparison column so the numbers don't drift?"
                ),
            },
            # ---- research confirms --------------------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 250,
                "reasoning": "Takes ownership.",
                "message": "Yes. I'll tag myself on the ticket and refresh it weekly until launch.",
            },
            # ---- narrator closes ----------------------------------------
            {
                "kind": "outro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 500,
                "message": None,
                "reasoning": "Wrapping the session.",
            },
        ],
    },
    # =======================================================================
    # Telemetry stack
    # =======================================================================
    "telemetry-stack": {
        "title": "Telemetry for NERVIX",
        "agenda": "Design a telemetry stack that shows us what our agents do in production — traces, metrics, one pane of glass.",
        "kickoff": "Production traffic hits next week. We leave this room with a named architecture and a ticket in David's queue.",
        "task": "Design a telemetry stack to track agent runs, errors, and latency.",
        "agents": ["research", "architect", "writer"],
        "problem": (
            "NERVIX has no way to see what its agents are doing in production. "
            "We need traces, metrics, and a single pane of glass before launch."
        ),
        "deliverable": ("a Langfuse-plus-OpenTelemetry architecture and an implementation ticket for David."),
        "why_assigned": {
            "research": (
                "Hi, I'm Dexter. I'm in this meeting because I've already benchmarked the observability tools we might pick. "
                "I'll tell you what passes our constraints and what doesn't."
            ),
            "architect": (
                "Hi, I'm David, systems architect. I'm here to take the tool choice and tell you exactly where it wires in — "
                "which layer, which boundary, and what it costs to build."
            ),
            "writer": (
                "Hi, I'm Hermes. I'm here to turn David's design into a ticket he can start on Monday. "
                "Concrete acceptance criteria, realistic estimates."
            ),
        },
        "turns": [
            {
                "kind": "intro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 200,
                "message": None,
                "reasoning": "Framing the problem and the roster.",
            },
            # ---- research ------------------------------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 500,
                "reasoning": "Benchmark four candidates against the constraints.",
                "message": (
                    "Dexter to start. I benchmarked four tools against our constraints — self-hostable, LLM-aware, friendly to token and cost metadata. "
                    "Langfuse is the strongest fit. Open source, purpose-built for agent runs, first-class prompt versioning. "
                    "Helicone is quick to set up but hosted only, so it fails our sovereignty bar. "
                    "Honeycomb is beautiful and priced for a Series B — not us. "
                    "For infrastructure metrics, we still need OpenTelemetry wired into a time-series backend. "
                    "David — does Langfuse plus OTel actually work together in practice, or am I picking a pretty prototype?"
                ),
            },
            # ---- architect answers --------------------------------------
            {
                "kind": "speak",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 350,
                "reasoning": "Confirm and sketch the wiring.",
                "message": (
                    "It works, Dexter, and I can tell you exactly where it plugs in. Two boundaries. "
                    "At the LLM boundary, we wrap litellm with a Langfuse callback — every completion emits a trace with input, output, token counts, and cost. "
                    "At the runtime boundary, we add OpenTelemetry spans for tool calls, pipeline steps, and memory reads. "
                    "The trick is we propagate the Langfuse trace ID into the OTel baggage, so one click in Grafana jumps you into the Langfuse run."
                ),
            },
            # ---- research probes ----------------------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 300,
                "reasoning": "Ask about cost and failure modes.",
                "message": (
                    "Two things I want to nail down. One, what's the rollout cost in engineer-days? "
                    "And two, what happens if Langfuse is down — do agent runs block?"
                ),
            },
            # ---- architect answers both ---------------------------------
            {
                "kind": "speak",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 300,
                "reasoning": "Answers both: two days, async sink.",
                "message": (
                    "Two engineer-days, roughly. One for the callback, one for the OTel instrumentation and Grafana dashboard. "
                    "On the outage question — the Langfuse callback is async, fire-and-forget. If Langfuse is unreachable we drop events, we don't block the run. "
                    "I'll put a circuit-breaker on it so we don't retry ourselves into a latency spike."
                ),
            },
            # ---- handoff ------------------------------------------------
            {
                "kind": "handoff",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 200,
                "message": "Hermes, that's enough to write a clean ticket.",
            },
            # ---- writer closes ------------------------------------------
            {
                "kind": "speak",
                "agent": "writer",
                "voice": VOICES["writer"],
                "delay_ms": 400,
                "reasoning": "Ticket with the agreed criteria.",
                "message": (
                    "Writing it up now. Title: Add Langfuse and OpenTelemetry to the NERVIX runtime. "
                    "Acceptance criteria. One: every agent run produces a Langfuse trace with token and cost metadata. "
                    "Two: OTel spans cover tool calls, pipeline steps, and memory reads, with trace IDs propagated into Langfuse. "
                    "Three: Grafana dashboard shows p50 and p95 latency per agent, plus error rate broken down by tool. "
                    "Four: the Langfuse callback degrades gracefully when the service is unreachable. "
                    "Scoped at two days with one day of buffer, per David."
                ),
            },
            {
                "kind": "outro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 500,
                "message": None,
                "reasoning": "Wrapping the session.",
            },
        ],
    },
    # =======================================================================
    # API design review
    # =======================================================================
    "api-design-review": {
        "title": "Public API Design",
        "agenda": "Design a public REST API for the NERVIX agent marketplace that third-party developers will actually enjoy using.",
        "kickoff": "The partner engineering team is blocked. We leave this room with an OpenAPI spec and a versioning policy.",
        "task": "Design a developer-friendly public API for the NERVIX marketplace.",
        "agents": ["research", "architect", "writer"],
        "problem": (
            "NERVIX needs a public API that third-party developers can use to list, "
            "discover, and invoke agents. It must be predictable, versioned, and well-documented."
        ),
        "deliverable": ("an OpenAPI 3.1 spec, a versioning policy, and a ticket for the gateway team."),
        "why_assigned": {
            "research": (
                "Hi, I'm Dexter. I'm here because I surveyed twenty public APIs developers love "
                "and twenty they hate. I'll tell you which patterns to steal and which to avoid."
            ),
            "architect": (
                "Hi, I'm David. I'm here to make sure the API we design actually fits our gateway, "
                "auth layer, and rate-limiting infrastructure without a rewrite in six months."
            ),
            "writer": (
                "Hi, I'm Hermes. I'm here to turn the design into a spec the gateway team can implement "
                "and developers can read without calling support."
            ),
        },
        "turns": [
            {
                "kind": "intro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 200,
                "message": None,
                "reasoning": "Framing the problem and the roster.",
            },
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 500,
                "reasoning": "Survey results: what developers expect.",
                "message": (
                    "Dexter here. I looked at the APIs developers praise most: Stripe, GitHub, Twilio. "
                    "Three patterns repeat. One: idempotency keys on every mutating endpoint. "
                    "Two: expandable fields so the default response is small but the detail is one query param away. "
                    "Three: explicit error codes with human-readable messages, not HTTP status guessing. "
                    "David — can our gateway support idempotency keys without a custom middleware stack?"
                ),
            },
            {
                "kind": "speak",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 350,
                "reasoning": "Confirm gateway capabilities and suggest rate-limit headers.",
                "message": (
                    "Yes, but with one caveat. Our current gateway is Envoy-based; idempotency needs a Redis-backed cache. "
                    "That's a day of work, not a week. I'd also propose we adopt the RateLimit header draft — "
                    "developers love knowing when they'll hit the wall before they hit it. "
                    "On versioning, I recommend URL path versioning, not headers. It's cache-friendly and obvious."
                ),
            },
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 300,
                "reasoning": "Push back on URL versioning with evidence.",
                "message": (
                    "One check. Stripe uses URL versioning and it's fine. GitHub uses media-type versioning in headers "
                    "and developers complain constantly. So URL path wins on developer experience. Accepted."
                ),
            },
            {
                "kind": "handoff",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 200,
                "message": "Hermes, over to you for the spec.",
            },
            {
                "kind": "speak",
                "agent": "writer",
                "voice": VOICES["writer"],
                "delay_ms": 400,
                "reasoning": "Draft OpenAPI spec outline and acceptance criteria.",
                "message": (
                    "Writing it up. Title: NERVIX Public API v1 — OpenAPI spec and gateway ticket. "
                    "Acceptance criteria. One: OpenAPI 3.1 spec covers list agents, get agent, invoke agent, and list runs. "
                    "Two: every mutating endpoint accepts an Idempotency-Key header. "
                    "Three: response includes RateLimit-Limit, RateLimit-Remaining, and RateLimit-Reset headers. "
                    "Four: URL path versioning at /v1/… with a deprecation policy documented in the spec. "
                    "Five: error schema is consistent across all endpoints with code, message, and doc_url. "
                    "David — one day for Redis idempotency, or should I scope it at two?"
                ),
            },
            {
                "kind": "speak",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 250,
                "reasoning": "Confirm estimate.",
                "message": "One day is realistic if we reuse the existing Redis connection pool. Scope it at one day plus half a day of buffer.",
            },
            {
                "kind": "outro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 500,
                "message": None,
                "reasoning": "Wrapping the session.",
            },
        ],
    },
    # =======================================================================
    # Launch readiness go/no-go
    # =======================================================================
    "launch-readiness": {
        "title": "NERVIX Launch Go/No-Go",
        "agenda": "Review launch blockers, sign off on readiness, and decide whether we ship on Monday or delay.",
        "kickoff": "Launch is scheduled for Monday. We leave this room with a clear go or no-go decision and a hot-fix list if needed.",
        "task": "Decide if NERVIX is ready to launch on Monday.",
        "agents": ["research", "strategist", "architect"],
        "problem": (
            "NERVIX is scheduled to launch Monday but there are open questions about "
            "performance, pricing page accuracy, and support readiness."
        ),
        "deliverable": ("a go/no-go decision with a ranked hot-fix list and an owner for each item."),
        "why_assigned": {
            "research": (
                "Hi, I'm Dexter. I'm here because I have the load-test numbers and the bug tally. "
                "I'll give you the facts without the optimism."
            ),
            "strategist": (
                "Hi, I'm Memo. I'm here to weigh the business cost of delaying against the reputation cost of shipping broken."
            ),
            "architect": (
                "Hi, I'm David. I'm here to tell you which technical risks are real and which are noise. "
                "If we go, I'll tell you exactly what to monitor."
            ),
        },
        "turns": [
            {
                "kind": "intro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 200,
                "message": None,
                "reasoning": "Framing the problem and the roster.",
            },
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 500,
                "reasoning": "Present load-test and bug data.",
                "message": (
                    "Dexter. Load-test results: p95 latency is 340ms at 500 concurrent users. That's under our 500ms target. "
                    "However, error rate spikes to 2.1 percent when we hit 800 concurrent. The spike is all database connection pool exhaustion. "
                    "Bug tally: seven open P1s, three of which are in the checkout flow. Two P1s have fixes ready but not deployed. "
                    "David — is the connection pool a config change or a code change?"
                ),
            },
            {
                "kind": "speak",
                "agent": "architect",
                "voice": VOICES["architect"],
                "delay_ms": 350,
                "reasoning": "Assess technical risks.",
                "message": (
                    "Config change. We can raise the pool from twenty to fifty and add a circuit breaker on the checkout service. "
                    "That's a two-hour change and a rolling restart. The P1s with fixes ready — if we deploy them today, we drop to four open P1s. "
                    "Of those four, two are edge cases that affect less than one percent of users. The other two are pricing page display bugs. "
                    "My read: the infra is ready if we make the pool change. The product has surface-level bugs, not structural ones."
                ),
            },
            {
                "kind": "speak",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 400,
                "reasoning": "Business judgment on delay vs ship.",
                "message": (
                    "Memo here. The business cost of a one-week delay is roughly forty thousand in deferred revenue and partner momentum. "
                    "The reputation cost of shipping with pricing page bugs is higher — one tweet from a confused user scales faster than a quiet delay. "
                    "My proposal: go, but with a hard requirement that pricing page P1s are fixed and verified before Monday 08:00. "
                    "If they slip, we delay forty-eight hours, not a full week. Dexter — can you own the pass-fail checklist?"
                ),
            },
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 300,
                "reasoning": "Take ownership of checklist.",
                "message": (
                    "Yes. I'll publish the checklist by end of day and run the final load test after the pool change. "
                    "If p95 stays under 400ms and error rate stays under 0.5 percent at 800 concurrent, I mark infra green. "
                    "If not, I flag red by 18:00 today."
                ),
            },
            {
                "kind": "outro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 500,
                "message": None,
                "reasoning": "Wrapping the session.",
            },
        ],
    },
    # =======================================================================
    # CrawdBot pricing
    # =======================================================================
    "crawdbot-pricing": {
        "title": "CrawdBot Pricing Model",
        "agenda": "Design a pricing model that's welcoming to beginners and fair to heavy users — with predictable billing.",
        "kickoff": "The pricing page is blocking launch. We leave this room with a three-tier proposal and a ticket for the site team.",
        "task": "Figure out a pricing model for CrawdBot that rewards heavy users without scaring beginners.",
        "agents": ["research", "strategist", "writer"],
        "problem": (
            "CrawdBot needs pricing that welcomes first-time users and is fair "
            "to heavy users, without the churn pattern we see in credit-based competitors."
        ),
        "deliverable": ("a three-tier pricing model and a ticket for the pricing page rewrite."),
        "why_assigned": {
            "research": (
                "Hi, I'm Dexter. I'm in this meeting because I pulled the churn numbers from three competitors. "
                "I'll tell you what actually makes people leave."
            ),
            "strategist": (
                "Hi, I'm Memo. I'm here to turn Dexter's churn data into a pricing model we can defend to the board — "
                "predictable for beginners, fair to whales."
            ),
            "writer": (
                "Hi, I'm Hermes. I'm here to turn the model into a pricing page ticket, plus copy guidelines so the language stays human."
            ),
        },
        "turns": [
            {
                "kind": "intro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 200,
                "message": None,
                "reasoning": "Framing the problem and the roster.",
            },
            # ---- research opens -----------------------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 500,
                "reasoning": "Competitors and their churn patterns.",
                "message": (
                    "Dexter. Here's the landscape. Firecrawl prices in credits. Apify in compute units. Bright Data in bandwidth. "
                    "I pulled churn patterns from three public write-ups. Two findings matter. "
                    "One, beginners leave credit-based products because they can't predict their bill. "
                    "Two, heavy users leave bandwidth-based products because retries double the invoice. "
                    "So the gap is pricing that's predictable at low volume and fair at high volume. Memo?"
                ),
            },
            # ---- strategist proposes -----------------------------------
            {
                "kind": "speak",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 400,
                "reasoning": "Propose a hybrid: generous free, flat mid, metered above.",
                "message": (
                    "Good framing. My proposal is a hybrid. Tier one: free, five hundred fetches a month — generous enough that hobbyists never hit the wall. "
                    "Tier two: flat nineteen dollars, twenty-five thousand fetches included. Tier three: metered, but only above that ceiling, at a transparent per-thousand rate. "
                    "Beginners get predictability, the middle feels lightly taxed, whales pay their share."
                ),
            },
            # ---- research pushes back -----------------------------------
            {
                "kind": "speak",
                "agent": "research",
                "voice": VOICES["research"],
                "delay_ms": 300,
                "reasoning": "Challenge the ceiling with churn data.",
                "message": (
                    "One concern, Memo. The churn data said the risk is at the ceiling — the moment a user gets a surprise invoice, they leave. "
                    "Twenty-five thousand is a lot, but some users will blow past it. What's the soft landing?"
                ),
            },
            # ---- strategist refines -------------------------------------
            {
                "kind": "speak",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 300,
                "reasoning": "Adds a pre-paid estimator and email warnings.",
                "message": (
                    "Two things. First, an estimator on the pricing page itself — enter your expected monthly fetches, see your projected bill before you sign up. "
                    "Second, at eighty percent of the ceiling we send an email. No surprise invoices. "
                    "Hermes, those both need to be acceptance criteria on the ticket, not footnotes."
                ),
            },
            # ---- handoff ------------------------------------------------
            {
                "kind": "handoff",
                "agent": "strategist",
                "voice": VOICES["strategist"],
                "delay_ms": 200,
                "message": "Your stage, Hermes.",
            },
            # ---- writer closes ------------------------------------------
            {
                "kind": "speak",
                "agent": "writer",
                "voice": VOICES["writer"],
                "delay_ms": 400,
                "reasoning": "Ticket with estimator and soft landing.",
                "message": (
                    "Drafting. Title: CrawdBot pricing — generous free, flat mid, metered above, no surprises. "
                    "Acceptance criteria. One: three tiers clearly named with one-sentence descriptions. "
                    "Two: calculator widget showing estimated monthly spend from projected fetches. "
                    "Three: email alert at eighty percent of the ceiling. "
                    "Four: the FAQ answers 'what happens when I hit the cap?' as the first question, not the last. "
                    "Copy guideline: no credit language, no compute units. Fetches, dollars, and a cap."
                ),
            },
            {
                "kind": "outro",
                "agent": "narrator",
                "voice": VOICES["narrator"],
                "delay_ms": 500,
                "message": None,
                "reasoning": "Wrapping the session.",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _materialize(scenario_id: str, scenario: dict) -> dict:
    problem = scenario.get("problem", "")
    deliverable = scenario.get("deliverable", "")
    agents = scenario.get("agents", [])

    turns = []
    for t in scenario.get("turns", []):
        t2 = dict(t)
        if t2.get("kind") == "intro" and not t2.get("message"):
            t2["message"] = _intro_script(problem, agents)
        elif t2.get("kind") == "outro" and not t2.get("message"):
            t2["message"] = _outro_script(deliverable)
        # Attach prosody for the TTS layer
        role = t2.get("agent")
        if role in VOICE_PROSODY:
            p = VOICE_PROSODY[role]
            t2.setdefault("rate", p["rate"])
            t2.setdefault("pitch", p["pitch"])
        turns.append(t2)

    # Build the lobby / meeting card
    attendees = []
    for a in agents:
        attendees.append(
            {
                "id": a,
                "name": DISPLAY_NAMES.get(a, a),
                "role": ROLE_LABELS.get(a, a),
                "voice": VOICES.get(a),
                "emoji": AGENT_EMOJI.get(a, "🤖"),
                "why": scenario.get("why_assigned", {}).get(a, ""),
            }
        )

    return {
        "id": scenario_id,
        "title": scenario["title"],
        "task": scenario["task"],
        "agents": agents,
        "problem": problem,
        "deliverable": deliverable,
        "voices": VOICES,
        "display": DISPLAY_NAMES,
        "prosody": VOICE_PROSODY,
        "meeting_card": {
            "title": scenario["title"],
            "agenda": scenario.get("agenda", problem),
            "kickoff": scenario.get("kickoff", ""),
            "attendees": attendees,
        },
        "why_assigned": scenario.get("why_assigned", {}),
        "turns": turns,
    }


def list_scenarios() -> list[dict]:
    return [
        {
            "id": sid,
            "title": s["title"],
            "task": s["task"],
            "agents": s["agents"],
        }
        for sid, s in SCENARIOS.items()
    ]


def get_scenario(scenario_id: str) -> dict | None:
    s = SCENARIOS.get(scenario_id)
    if not s:
        return None
    return _materialize(scenario_id, s)


def get_why_assigned(scenario_id: str, agent_id: str) -> dict | None:
    """Return the bio payload for a single agent (used by the /api/tts click)."""
    s = SCENARIOS.get(scenario_id)
    if not s:
        return None
    text = s.get("why_assigned", {}).get(agent_id)
    if not text:
        return None
    return {
        "agent": agent_id,
        "name": DISPLAY_NAMES.get(agent_id, agent_id),
        "role": ROLE_LABELS.get(agent_id, agent_id),
        "voice": VOICES.get(agent_id),
        "rate": VOICE_PROSODY.get(agent_id, {}).get("rate", "+0%"),
        "pitch": VOICE_PROSODY.get(agent_id, {}).get("pitch", "+0Hz"),
        "text": text,
    }
