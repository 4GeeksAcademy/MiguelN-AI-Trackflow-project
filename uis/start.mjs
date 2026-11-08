import { spawn } from "node:child_process";

const apps = [
  { name: "website", port: "3000" },
  { name: "backoffice", port: "3001" },
];

const children = apps.map(({ name, port }) => ({
  name,
  process: spawn(
    process.execPath,
    ["node_modules/next/dist/bin/next", "dev", "--hostname", "0.0.0.0", "--port", port],
    {
      cwd: `/workspace/${name}`,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_URL:
          name === "website" ? process.env.NEXT_PUBLIC_RECORDS_API_URL : "/backend",
      },
      stdio: "inherit",
    },
  ),
  closed: false,
}));

let stopping = false;
let closedCount = 0;

function stop(signal) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (child.process.exitCode === null && child.process.signalCode === null) {
      child.process.kill(signal);
    }
  }
}

for (const child of children) {
  child.process.on("error", (error) => {
    console.error(`${child.name} failed to start:`, error);
    process.exitCode = 1;
    stop("SIGTERM");
  });

  child.process.on("close", (code) => {
    child.closed = true;
    closedCount += 1;
    if (!stopping) {
      process.exitCode = code || 1;
      stop("SIGTERM");
    }
    if (closedCount === children.length) process.exit();
  });
}

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));