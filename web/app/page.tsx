export const dynamic = "force-dynamic";

export default async function Page() {
  const base = process.env.NEXT_PUBLIC_API_BASE!;
  const res = await fetch(`${base}/decisions?limit=5`, { cache: "no-store" });
  const data = await res.json();
  return (
    <main className="p-8 font-mono text-sm">
      <h1 className="mb-4 text-lg">Options Alpha Agent — live state</h1>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </main>
  );
}
