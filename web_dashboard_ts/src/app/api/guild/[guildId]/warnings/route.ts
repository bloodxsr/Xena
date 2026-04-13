import { NextRequest, NextResponse } from "next/server";
import { listWarningCounts } from "@/lib/db";
import { requireGuildContext } from "@/lib/request-auth";

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

  const limitParam = Number(request.nextUrl.searchParams.get("limit") || "50");
  const warnings = await listWarningCounts(guildId, Number.isFinite(limitParam) ? limitParam : 50);
  return NextResponse.json({ warnings });
}
