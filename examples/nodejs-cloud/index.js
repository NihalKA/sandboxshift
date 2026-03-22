/**
 * Node.js cloud example — Express server on AWS Fargate.
 *
 * Identical app to nodejs-local but configured to always run in cloud.
 * Useful when the server needs more RAM than your laptop can spare,
 * or when you want it reachable from outside your machine.
 *
 * ---------------------------------------------------------------------------
 * Running with YAML config (uses sandboxshift.yaml in this directory):
 *
 *   sandboxshift run examples/nodejs-cloud "node index.js"
 *
 * Running with CLI flags only (no YAML needed):
 *
 *   sandboxshift run examples/nodejs-cloud "node index.js" \\
 *     --mode cloud \\
 *     --port 3000 \\
 *     --cpu 1.0 \\
 *     --memory-mb 2048 \\
 *     --timeout 3600 \\
 *     --setup "npm ci"
 *
 * Once running, the instance ID and public URL are printed:
 *   sandboxshift list              → see all running cloud servers
 *   sandboxshift stop <id>         → stop when done
 *
 * Fargate: 1 vCPU supports 2–8 GB memory.
 * ---------------------------------------------------------------------------
 */

const express = require('express');

const app = express();
const PORT = process.env.PORT || 3000;   // SandboxShift injects PORT automatically

function fibonacci(n) {
  if (n <= 1) return n;
  return fibonacci(n - 1) + fibonacci(n - 2);
}

app.get('/', (req, res) => {
  res.json({ status: 'ok', runtime: process.version, env: 'fargate' });
});

app.get('/compute', (req, res) => {
  const n = Math.min(parseInt(req.query.n || '35', 10), 45);
  const start = Date.now();
  const result = fibonacci(n);
  const ms = Date.now() - start;
  res.json({ n, result, ms });
});

app.listen(PORT, '0.0.0.0', () => {
  // Bind to 0.0.0.0 so the Fargate task is reachable via its public IP.
  // For local runs SandboxShift binds the host side to 127.0.0.1 regardless.
  console.log(`Server listening on port ${PORT}`);
});
