import { NextRequest, NextResponse } from "next/server";
import { getGuildConfig, getRaidGateState, setRaidGateState } from "@/lib/db";
import { parseJsonBody } from "@/lib/http-body";
import { requireGuildContext, requireTotpAuthorization } from "@/lib/request-auth";

export const runtime = "nodejs";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ guildId: string }> }
): Promise<NextResponse> {
  const { guildId } = await context.params;
  const auth = await requireGuildContext(request, guildId);
  if (auth instanceof NextResponse) {
    return auth;
  }

  return NextResponse.json({ raidGate: await getRaidGateState(guildId) });
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ guildId: string }> }
): Promise<NextResponse> {
  const { guildId } = await context.params;
  const auth = await requireGuildContext(request, guildId);
  if (auth instanceof NextResponse) {
    return auth;
  }

  const totpCheck = await requireTotpAuthorization(guildId, auth.session.userId);
  if (!totpCheck.ok) {
    return NextResponse.json({ error: totpCheck.error }, { status: 403 });
  }

  const parsedBody = await parseJsonBody<{
    enabled?: boolean;
    reason?: string;
    durationSeconds?: number;
  }>(request);

  if (!parsedBody.ok) {
    return NextResponse.json({ error: parsedBody.error }, { status: 400 });
  }

  const payload = parsedBody.data;

  if (typeof payload.enabled !== "boolean") {
    return NextResponse.json({ error: "enabled flag is required." }, { status: 400 });
  }

  const cfg = await getGuildConfig(guildId);
  const duration = Math.max(60, Math.min(Number(payload.durationSeconds || cfg.gate_duration_seconds), 86400));
  const reason = payload.reason ? String(payload.reason).trim() : null;
  const gateUntil = payload.enabled ? new Date(Date.now() + duration * 1000).toISOString() : null;

  const raidGate = await setRaidGateState(guildId, payload.enabled, reason, gateUntil);
  return NextResponse.json({ raidGate });
}
