import { NextRequest, NextResponse } from "next/server";
import { runWorker } from "@/lib/python";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const strategyId = request.nextUrl.searchParams.get("strategy_id") || "";
    const count = Number(request.nextUrl.searchParams.get("count") || 90);
    return NextResponse.json(await runWorker("chart", { strategy_id: strategyId, count }));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Chart feed failed." }, { status: 400 });
  }
}
