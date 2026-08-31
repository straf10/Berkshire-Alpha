export function ServiceDown() {
  return (
    <main className="p-8 font-mono text-sm">
      <h1 className="mb-4 text-lg">Autonomous Debate Trading Agent</h1>
      <div className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-destructive">
        <span className="h-2 w-2 rounded-full bg-destructive" />
        <span className="font-semibold">Agent service is unreachable.</span>
        <span className="text-muted-foreground">Check back shortly.</span>
      </div>
    </main>
  );
}
