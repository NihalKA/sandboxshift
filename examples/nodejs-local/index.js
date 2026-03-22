/**
 * Node.js local example — simple Express HTTP server.
 *
 * Exposes two endpoints:
 *   GET /         → health check
 *   GET /compute  → synchronous Fibonacci to simulate light CPU work
 *
 * The server runs until the sandbox timeout kills it, or until you
 * Ctrl+C the sandboxshift process.
 *
 * ---------------------------------------------------------------------------
 * Running with YAML config (uses sandboxshift.yaml in this directory):
 *
 *   sandboxshift run examples/nodejs-local "node index.js"
 *
 * Running with CLI flags only (no YAML needed):
 *
 *   sandboxshift run examples/nodejs-local "node index.js" \\
 *     --mode local \\
 *     --port 3000 \\
 *     --cpu 1.0 \\
 *     --memory-mb 512 \\
 *     --timeout 3600 \\
 *     --setup "npm ci" \\
 *     --allow registry.npmjs.org
 *
 * Once running, test it from your host:
 *   curl http://localhost:3000/
 *   curl http://localhost:3000/compute?n=40
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
  res.json({ status: 'ok', runtime: process.version });
});

app.get('/compute', (req, res) => {
  const n = Math.min(parseInt(req.query.n || '35', 10), 45);  // cap at 45 for safety
  const start = Date.now();
  const result = fibonacci(n);
  const ms = Date.now() - start;
  res.json({ n, result, ms });
});

app.listen(PORT, () => {
  console.log(`Server listening on http://localhost:${PORT}`);
});
