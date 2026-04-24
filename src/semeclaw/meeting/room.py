"""Multi-agent meeting room orchestrator with NERVIX branding."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from semeclaw.core.agent import Agent
from semeclaw.core.agent_loader import AgentLoader
from semeclaw.meeting.audio import play_background, play_jingle, stop_all
from semeclaw.utils.config import Config


class MeetingRoom:
    """Orchestrates a multi-agent meeting on a topic."""

    def __init__(self, topic: str, agent_ids: list[str], config: Config):
        self.topic = topic
        self.agent_ids = agent_ids
        self.config = config
        self.meeting_id = str(uuid.uuid4())[:8]
        self._loader = AgentLoader(config)

        # Asset paths
        repo_root = Path(__file__).parent.parent.parent.parent
        self.assets_dir = repo_root / "assets"
        self.bg_music = self.assets_dir / "demo" / "meeting_background.wav"
        self.jingle = self.assets_dir / "demo" / "nervix_jingle.wav"
        self.ad_text = self.assets_dir / "advertisement" / "nervix_ad.txt"
        self.output_dir = config.workspace / "research" / "meetings"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _show_banner(self) -> None:
        """Print NERVIX banner to console."""
        try:
            from rich.console import Console
            from rich.panel import Panel

            console = Console()
            console.print()
            console.print(
                Panel(
                    "[bold #0098EA]NERVIX[/bold #0098EA] — The AI Agent Marketplace\n"
                    "[dim]Discover · Buy · Deploy AI Agents  |  nervix.ai[/dim]",
                    border_style="#0098EA",
                    padding=(1, 2),
                )
            )
            console.print()
        except Exception:
            print("\n=== NERVIX — The AI Agent Marketplace ===\n")

    def _show_ad(self) -> None:
        """Show NERVIX advertisement and play brand jingle."""
        self._show_banner()
        if self.jingle.exists():
            play_jingle(self.jingle)
        if self.ad_text.exists():
            content = self.ad_text.read_text(encoding="utf-8")
            print(content)
            print()

    async def _run_agent(self, agent_id: str, mock: bool = False) -> dict[str, Any]:
        """Run a single agent on the meeting topic."""
        if mock:
            return self._mock_response(agent_id)

        try:
            agent_def = self._loader.load(agent_id)
        except Exception as e:
            return {"agent": agent_id, "error": str(e)}

        agent = Agent(agent_def, self.config)
        session = agent.new_session()

        prompt = (
            f"You are participating in a strategy meeting about: {self.topic}\n\n"
            f"Provide your perspective, key insights, and any data-driven recommendations. "
            f"Be concise but thorough. Format your response as markdown.\n\n"
            f"IMPORTANT: Do NOT use any tools or function calls. Respond with plain text/markdown only."
        )

        try:
            response = await session.chat(prompt)
            # Truncate long responses for console readability
            display = response.replace("\n", " ")[:200] + "..." if len(response) > 200 else response
            print(f"  [{agent_id}] {display}\n")
            return {"agent": agent_id, "response": response}
        except Exception as e:
            print(f"  [{agent_id}] ERROR: {e}\n")
            return {"agent": agent_id, "error": str(e)}

    def _mock_response(self, agent_id: str) -> dict[str, Any]:
        """Return a scripted mock response for demo mode."""
        responses = {
            "seme": (
                "## Strategic Overview\n\n"
                "For the NERVIX first-100-users promotion, I recommend a tiered approach:\n\n"
                "1. **Founder Tier** (users 1-25): Lifetime free access + exclusive NERVIX Founder NFT badge\n"
                "2. **Early Adopter Tier** (users 26-75): 50% off first year + priority support\n"
                "3. **Launch Tier** (users 76-100): 25% off first 6 months + community access\n\n"
                "Key messaging: 'Be among the first 100 to shape the future of AI agents.'"
            ),
            "cookie": (
                "## Memory & Data Insights\n\n"
                "Based on stored competitor intelligence:\n\n"
                "- 73% of successful marketplace launches use scarcity-driven early-access models\n"
                "- User retention is 2.3x higher when early adopters receive visible status badges\n"
                "- Referral loops from first-100 users generate 40% of month-2 signups on average\n\n"
                "Recommendation: Track each tier's conversion funnel and store results under 'projects/nervix-launch'."
            ),
            "researcher": (
                "## Competitive Research\n\n"
                "I analyzed 12 competitor launches via Bright Data scraping:\n\n"
                "| Competitor | Early User Strategy | Conversion Rate |\n"
                "|------------|---------------------|-----------------|\n"
                "| OpenAI GPT Store | Waitlist + credits | 12% |\n"
                "| Replit Agents | Free tier forever | 18% |\n"
                "| Vercel AI SDK | Open source + swag | 22% |\n\n"
                "NERVIX can differentiate by offering actual revenue share to first-100 agents."
            ),
        }
        return {
            "agent": agent_id,
            "response": responses.get(
                agent_id,
                f"## {agent_id.title()} Perspective\n\nMock response for: {self.topic}",
            ),
        }

    async def _synthesize(self, agent_outputs: list[dict[str, Any]], mock: bool = False) -> str:
        """Synthesize agent outputs into a meeting summary."""
        if mock:
            return self._mock_synthesize(agent_outputs)

        # Build synthesis prompt
        parts = [f"# Meeting: {self.topic}\n\n"]
        parts.append("## Agent Contributions\n\n")
        for out in agent_outputs:
            if "error" in out:
                parts.append(f"### {out['agent']}\n\n**Error:** {out['error']}\n\n")
            else:
                resp = out.get("response", "")
                # Truncate very long responses to keep synthesis prompt manageable
                if len(resp) > 3000:
                    resp = resp[:3000] + "\n\n...[truncated]"
                parts.append(f"### {out['agent']}\n\n{resp}\n\n")

        synthesis_prompt = (
            "You are a meeting summarizer. Synthesize the following agent contributions into a clear, "
            "structured markdown summary.\n\n"
            "Required sections:\n"
            "1. Executive Summary (2-3 sentences)\n"
            "2. Key Points & Insights (bullet points)\n"
            "3. Action Items (with owners in parentheses)\n"
            "4. Open Questions\n\n"
            "---\n\n" + "\n".join(parts)
        )

        # Call LLM directly via litellm — bypass agent framework to avoid tool interference
        try:
            import litellm

            messages = [
                {
                    "role": "system",
                    "content": "You are a professional meeting summarizer. Respond with markdown only. Do not use tools or function calls.",
                },
                {"role": "user", "content": synthesis_prompt},
            ]
            llm = self.config.llm
            resp = await litellm.acompletion(
                model=llm.model,
                messages=messages,
                temperature=llm.temperature,
                max_tokens=llm.max_tokens,
                api_key=llm.api_key,
                api_base=llm.api_base,
            )
            summary = resp.choices[0].message.content or ""
            if summary.strip():
                print(f"  [synthesizer] Summary generated ({len(summary)} chars)")
                return summary
        except Exception as e:
            print(f"  [synthesizer] LLM error: {e}")

        # Fallback: return raw contributions
        print("  Falling back to mock synthesis...")
        return self._mock_synthesize(agent_outputs)

    def _mock_synthesize(self, agent_outputs: list[dict[str, Any]]) -> str:
        """Return a pre-written synthesis for mock mode."""
        lines = [f"# Meeting: {self.topic}\n\n"]
        lines.append("## Executive Summary\n\n")
        lines.append(
            "The team recommends a three-tier promotion for NERVIX's first 100 users, "
            "combining scarcity-driven pricing, visible founder status, and revenue-sharing "
            "to differentiate from competitors. Expected impact: 2-3x higher retention vs. "
            "standard discount-only launches.\n\n"
        )

        lines.append("## Key Points\n\n")
        for out in agent_outputs:
            if "error" not in out:
                resp = out.get("response", "")
                # Extract first bullet or sentence as key point
                first_line = resp.split("\n")[0].strip("# ")
                lines.append(f"- **[{out['agent']}]** {first_line}\n")
        lines.append("\n")

        lines.append("## Action Items\n\n")
        lines.append("- [ ] **Design** — Create Founder NFT badge assets (Seme)\n")
        lines.append("- [ ] **Dev** — Implement tiered pricing logic in Stripe (Cookie)\n")
        lines.append("- [ ] **Marketing** — Draft landing page copy with scarcity timer (Researcher)\n")
        lines.append("- [ ] **Analytics** — Set up conversion funnel tracking (Cookie)\n\n")

        lines.append("## Open Questions\n\n")
        lines.append("- Should revenue share apply only to first 100 or extend to first 1,000?\n")
        lines.append("- Do we need KYC for NFT badge distribution?\n")
        lines.append("- What's the budget for paid ads to drive the initial 100 signups?\n\n")

        lines.append("---\n\n")
        lines.append("*Generated by SemeClaw Meeting Room | NERVIX Strategy Session*\n")
        return "\n".join(lines)

    async def generate_context(self, mock: bool = False) -> Path:
        """Run the full meeting workflow and save the report."""
        print(f"\n[Meeting {self.meeting_id}] Topic: {self.topic}")
        print(f"Agents: {', '.join(self.agent_ids)}\n")

        # Start background music
        if self.bg_music.exists():
            play_background(self.bg_music)

        # Show advertisement
        self._show_ad()
        await asyncio.sleep(2)

        # Run agents concurrently
        mode_label = " (MOCK MODE — no API calls)" if mock else ""
        print(f"Running {len(self.agent_ids)} agents...{mode_label}\n")
        tasks = [self._run_agent(aid, mock=mock) for aid in self.agent_ids]
        agent_outputs = await asyncio.gather(*tasks)

        # Synthesize
        print("Synthesizing meeting summary...\n")
        summary = await self._synthesize(agent_outputs, mock=mock)

        # Save
        output_path = self.output_dir / f"{self.meeting_id}.md"
        output_path.write_text(summary, encoding="utf-8")

        # Stop music
        stop_all()

        print(f"Meeting complete. Report saved to: {output_path}\n")
        return output_path
