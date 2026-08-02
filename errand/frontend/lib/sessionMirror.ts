export async function mirrorSessionToken(token: string | null): Promise<boolean> {
  try {
    const response = await fetch(
      "/api/session",
      token !== null
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token }),
          }
        : { method: "DELETE" },
    );
    return response.ok;
  } catch {
    return false;
  }
}
