import { cookies } from "next/headers";
import { NextResponse } from "next/server";

const COOKIE = "errand_session";
const MAX_AGE = 60 * 60 * 24 * 7;

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as { token?: unknown } | null;
  if (!body || typeof body.token !== "string" || body.token.length < 20 || body.token.length > 4096) {
    return NextResponse.json({ detail: "Invalid session token" }, { status: 400 });
  }
  const store = await cookies();
  store.set(COOKIE, body.token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV !== "development",
    path: "/",
    maxAge: MAX_AGE,
  });
  return new NextResponse(null, { status: 204 });
}

export async function DELETE() {
  const store = await cookies();
  store.set(COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV !== "development",
    path: "/",
    maxAge: 0,
  });
  return new NextResponse(null, { status: 204 });
}
