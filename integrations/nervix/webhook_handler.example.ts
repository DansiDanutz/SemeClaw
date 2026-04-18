/**
 * NERVIX — SemeClaw webhook receiver (reference implementation)
 *
 * Receives lifecycle events from SemeClaw:
 *   - report.created
 *   - meeting.finalized
 *   - paperclip.triggered
 *
 * Drop this into your NERVIX backend at /api/semeclaw/webhook.
 *
 * Requires env:
 *   SEMECLAW_WEBHOOK_SECRET — the secret you registered with SemeClaw
 */
import crypto from "node:crypto";
import type { Request, Response } from "express";

type SemeClawEvent = {
  event: string;
  ts: string;
  agent_version: string;
  tenant_id: string;
  data: Record<string, unknown>;
};

function verifySignature(rawBody: string, header: string | undefined, secret: string): boolean {
  if (!header) return false;
  const [algo, sig] = header.split("=");
  if (algo !== "sha256" || !sig) return false;
  const expected = crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  if (sig.length !== expected.length) return false;
  return crypto.timingSafeEqual(Buffer.from(sig, "hex"), Buffer.from(expected, "hex"));
}

export async function handleSemeClawWebhook(req: Request, res: Response) {
  const secret = process.env.SEMECLAW_WEBHOOK_SECRET ?? "";
  if (!secret) {
    return res.status(500).json({ error: "SEMECLAW_WEBHOOK_SECRET not configured" });
  }

  const raw = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
  const sig = req.headers["x-semeclaw-signature"] as string | undefined;
  if (!verifySignature(raw, sig, secret)) {
    return res.status(401).json({ error: "invalid signature" });
  }

  const payload = JSON.parse(raw) as SemeClawEvent;
  const userId = payload.tenant_id.replace(/^nervix-user-/, "");

  switch (payload.event) {
    case "meeting.finalized": {
      const { name, verdict_line, qa_count } = payload.data as {
        name: string;
        verdict_line: string;
        qa_count: number;
      };
      // Pull the updated markdown from SemeClaw + append to the source NERVIX task
      const md = await fetch(
        `${process.env.SEMECLAW_URL}/api/reports/content?name=${encodeURIComponent(name)}`,
        { headers: { "X-Tenant-Id": payload.tenant_id } },
      ).then((r) => r.json());

      await nervix.tasks.appendActivity(userId, {
        type: "meeting.finalized",
        verdict: verdict_line,
        qa_count,
        markdown: md.content,
        agent: "semeclaw-war-room",
      });

      await nervix.notifications.push(userId, {
        title: "War Room complete",
        body: verdict_line,
        deep_link: `/tasks/${name.replace(".md", "")}`,
      });
      break;
    }

    case "report.created":
      await nervix.audit.log(userId, "semeclaw.report_created", payload.data);
      break;

    case "paperclip.triggered":
      // For observability — a NERVIX task just convened a meeting
      await nervix.audit.log(userId, "semeclaw.meeting_started", payload.data);
      break;

    default:
      // Unknown event — log and ignore
      console.log("[semeclaw] unknown event:", payload.event);
  }

  res.status(200).json({ ok: true });
}

// --------- pseudo-NERVIX client (replace with your real one) ---------
declare const nervix: {
  tasks: { appendActivity(userId: string, data: Record<string, unknown>): Promise<void> };
  notifications: { push(userId: string, data: Record<string, unknown>): Promise<void> };
  audit: { log(userId: string, action: string, data: Record<string, unknown>): Promise<void> };
};
