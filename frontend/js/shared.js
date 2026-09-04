export async function listRuns() {
    const res = await fetch("/api/runs");
    if (!res.ok)
        throw new Error(`/api/runs ${res.status}`);
    return (await res.json());
}
