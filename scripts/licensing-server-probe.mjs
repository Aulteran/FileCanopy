// Local disposable compatibility evaluation only; no Compose or application replacement.
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";

const [serverRef, apiRef, output] = process.argv.slice(2);
if (!output)
  throw new Error("Supply server image, API image and output JSON path");
const run = (...args) =>
  execFileSync("docker", args, {
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
  }).trim();
const server = JSON.parse(run("image", "inspect", serverRef))[0];
const api = JSON.parse(run("image", "inspect", apiRef))[0];
const name = `filecanopy-license-${randomUUID()}`;
const owned = [];
let network;
const result = {
  accessed: new Date().toISOString(),
  server: {
    imageId: server.Id,
    digests: server.RepoDigests,
    platform: `${server.Os}/${server.Architecture}`,
  },
  api: api.Id,
  checks: [],
};
const client = (code) =>
  run(
    "run",
    "--rm",
    "--pull=never",
    "--network",
    name,
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--entrypoint",
    "python",
    api.Id,
    "-c",
    code,
  );
const setup =
  "import time, redis; from app.health import check_optional, WORKER_HEARTBEAT_PREFIX; c=redis.Redis.from_url('redis://server:6379/0',socket_timeout=2,decode_responses=True); ";
try {
  network = run("network", "create", "--internal", name);
  const id = run(
    "run",
    "-d",
    "--pull=never",
    "--network",
    name,
    "--network-alias",
    "server",
    "--read-only",
    "--tmpfs",
    "/data",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--user",
    "valkey",
    server.Id,
    "valkey-server",
    "--save",
    "",
    "--appendonly",
    "no",
  );
  owned.push(id);
  result.checks.push(
    client(
      setup +
        "assert c.ping(); assert check_optional('')[0].state == 'not_configured'; assert check_optional('redis://server:6379/0')[1].reason == 'no_worker_heartbeat'; print('PING and optional absent/no-heartbeat states pass')",
    ),
  );
  const worker = run(
    "run",
    "-d",
    "--pull=never",
    "--network",
    name,
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "-e",
    "FILECANOPY_REDIS_URL=redis://server:6379/0",
    api.Id,
    "python",
    "-m",
    "workers.heartbeat",
  );
  owned.push(worker);
  result.checks.push(
    client(
      setup +
        "\nfor _ in range(15):\n keys=list(c.scan_iter(match=WORKER_HEARTBEAT_PREFIX+'*', count=100))\n if keys: break\n time.sleep(1)\nassert len(keys)==1; assert 0 < c.ttl(keys[0]) <= 30; assert all(s.state=='ok' for s in check_optional('redis://server:6379/0')); print('real worker SET EX, SCAN, TTL and application health pass')",
    ),
  );
  run("stop", "--time", "5", worker);
  result.checks.push(
    client(
      setup +
        "\nfor _ in range(35):\n if not list(c.scan_iter(match=WORKER_HEARTBEAT_PREFIX+'*')): break\n time.sleep(1)\nassert not list(c.scan_iter(match=WORKER_HEARTBEAT_PREFIX+'*')); assert check_optional('redis://server:6379/0')[1].reason=='no_worker_heartbeat'; print('SIGTERM stops worker; actual 30-second heartbeat expiry detected')",
    ),
  );
  run("stop", "--time", "5", id);
  result.checks.push(
    client(
      setup +
        "a,b=check_optional('redis://server:6379/0'); assert a.state==b.state=='unavailable'; print('server unavailable is handled by bounded optional probe')",
    ),
  );
  run("start", id);
  run("start", worker);
  result.checks.push(
    client(
      setup +
        "\nfor _ in range(15):\n if all(s.state=='ok' for s in check_optional('redis://server:6379/0')): break\n time.sleep(1)\nassert all(s.state=='ok' for s in check_optional('redis://server:6379/0')); print('restart and heartbeat recovery pass')",
    ),
  );
  result.passed = true;
} finally {
  for (const id of owned.reverse()) run("rm", "-f", id);
  if (network) run("network", "rm", network);
  writeFileSync(output, JSON.stringify(result, null, 2) + "\n");
}
