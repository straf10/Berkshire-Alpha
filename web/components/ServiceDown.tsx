import { CircleAlert } from "lucide-react";

export function ServiceDown() {
  return (
    <main className="p-4 font-mono text-base sm:p-8">
      <h1 className="mb-4 text-xl sm:text-2xl">Autonomous Debate Trading Agent</h1>
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-destructive">
        <CircleAlert className="size-4" />
        <span className="font-semibold">Agent service is unreachable.</span>
        <span className="text-muted-foreground">Check back shortly.</span>
      </div>
    </main>
  );
}
