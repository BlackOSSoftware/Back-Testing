import { NextRequest, NextResponse } from "next/server";
import { runWorker } from "@/lib/python";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const query = request.nextUrl.searchParams.get("q") || "";
    return NextResponse.json(await runWorker("symbols", { query }));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 500 });
  }
}
