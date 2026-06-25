import { NextRequest, NextResponse } from "next/server";
import { runWorker } from "@/lib/python";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json(await runWorker("strategies"));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json();
    return NextResponse.json(await runWorker("strategy_create", payload));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 400 });
  }
}
