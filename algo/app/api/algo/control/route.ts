import { NextRequest, NextResponse } from "next/server";
import { runWorker } from "@/lib/python";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const payload = await request.json();
    return NextResponse.json(await runWorker("control", payload));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 400 });
  }
}
