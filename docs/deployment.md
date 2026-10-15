# FreshRoute LastWindow deployment

## Deployment architecture

One Railway Docker service, one replica, one Uvicorn worker, and a persistent volume mounted at `/data`. FastAPI serves both the API and the compiled React application. No separate frontend service is required.

The repository root must be the build context. The multi-stage Dockerfile compiles the frontend with Node 24, then installs the pinned backend dependencies in Python 3.11. It copies only the runtime application, configurations, synthetic scenarios, and compiled interface. Local databases, videos, recordings, virtual environments, credentials, and test artifacts are excluded.

## Hosting configuration

- Source: the checked-out project, including Dockerfile, railway.json, and scripts/serve.py.
- Persistent volume: `/data`. Create this before the first production launch. The configuration file does not provision the volume automatically.
- `FRESHROUTE_DB_PATH=/data/freshroute.sqlite3` (already the Docker image default).
- `FRESHROUTE_OPERATOR_PASSWORD`: application password, at least 12 characters, supplied through the hosting secret store. The synthetic hackathon demo credential is shared through the private judging packet, not the public repository. Use a separate private password before hosting private operational data. Never commit hosting-provider credentials or personal account passwords.
- `FRESHROUTE_REQUIRE_AUTH=1` (image default). Startup refuses an absent or short password.
- `PORT`: supplied by Railway, read by the entrypoint.
- `FRESHROUTE_SECURE_COOKIES=1` (image default): session cookies require HTTPS, including when TLS terminates at the hosting ingress.
- `FORWARDED_ALLOW_IPS` is not broadened: the entrypoint defaults to loopback. The app does not require arbitrary proxy headers to be trusted.
- `FRESHROUTE_ALLOWED_ORIGINS`: set to the exact public HTTPS origin. This permits browser mutations when the hosting ingress terminates TLS before forwarding to the application.
- Health check: `/health`, timeout 120 seconds.
- Public HTTPS domain: generate once the service starts.
- Keep one replica and one worker. The current queue and authentication sessions are local to one API process.

## Verification after publication

1. Confirm a successful image build and `/health` response.
2. Confirm the home page and generated assets load over HTTPS.
3. Confirm API calls require login; sign in and verify the session cookie is Secure and HttpOnly.
4. Import the sample scenario, generate a nominal plan, inspect a receipt, and replay it.
5. Exercise reservation, route delay, recovery, and replanning.
6. Confirm saved scenarios and plans are retrievable in a fresh authenticated session. A separately authorized restart test can additionally check restart persistence. A service restart intentionally invalidates login sessions.

## Local equivalent

With Docker installed, build with `docker build -t freshroute-lastwindow .`. Run a single container with a named volume at `/data`, the operator password supplied through the environment, and port 8000 published. Do not run multiple containers against the same SQLite database.

## Deployment state

The live hackathon application is https://lastwindow-production.up.railway.app. Railway reported a successful deployment with one running replica and a 500 MB volume at `/data`. Source is `paarth293/Fresh-Route`, branch `deploy/railway`; pushes to that branch trigger deployment.

See [Judge access](../README.md#judge-access) for the demo credential and [the hosted verification report](../reports/deployment.md) for checks and limits. Verify provider status after each release.
