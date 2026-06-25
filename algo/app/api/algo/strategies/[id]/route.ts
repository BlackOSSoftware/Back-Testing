import { NextRequest, NextResponse } from "next/server";
import { runWorker } from "@/lib/python";

export const dynamic = "force-dynamic";

type Params = {
  params: Promise<{ id: string }>;
};

export async function PUT(request: NextRequest, { params }: Params) {
  try {
    const { id } = await params;
    const payload = await request.json();
    return NextResponse.json(await runWorker("strategy_update", { id, ...payload }));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 400 });
  }
}

export async function DELETE(_request: NextRequest, { params }: Params) {
  try {
    const { id } = await params;
    return NextResponse.json(await runWorker("strategy_delete", { id }));
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Request failed." }, { status: 400 });
  }
}
