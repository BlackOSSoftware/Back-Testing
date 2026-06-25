import { NextResponse } from "next/server";
import { runWorker } from "@/lib/python";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json(await runWorker("status"));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 500 });
  }
}
