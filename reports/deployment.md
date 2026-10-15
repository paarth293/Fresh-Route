# Live deployment verification

Public URL: https://lastwindow-production.up.railway.app

Provider: Railway. Project: `freshroute-lastwindow`. Service: `lastwindow`. Environment: `production`.

Deployed source: `paarth293/Fresh-Route`, branch `deploy/railway`, commit `264bf69a4d64af573354aaa9b0f81f625e85d3b2`.

Railway deployment `0cc1072c-d092-4a0e-88d9-58bea81f7886` reached SUCCESS. The final status check showed one running replica, zero crashed replicas, no pending work, and no active warnings or critical issues.

## Live checks completed

- Public HTTPS health response: 200, database ready, worker ready, SCIP configured.
- Actual rendered interface and assets loaded in Chromium.
- API rejected unauthenticated access.
- Browser login succeeded; session cookie was Secure and HttpOnly.
- Native background solver generated an independently validated plan.
- Batch decision receipt opened successfully.
- Simulated replay completed with inventory reconciliation.
- Mobile layout at 390px had no horizontal page overflow.
- Browser logout invalidated API access.
- No browser JavaScript runtime errors were observed.
- A new authenticated session retrieved the saved scenario and validated plan.

Persistent storage: 500 MB volume `lastwindow-data`, mounted at `/data`. SQLite path: `/data/freshroute.sqlite3`. A disruptive restart test was not performed; persistence was checked across authenticated sessions and the attached volume was confirmed through Railway.

The application's synthetic-demo password is stored in Railway's service variables and the local ignored file `work/deployment/Sign-in details.txt`. Judge access is provided through the private judging packet; the public README explains the access procedure. It is not a Railway, GitHub, or personal-account password.

Future pushes to `deploy/railway` trigger a new Railway deployment. The initial deployment used that separate branch; the submission documentation is also published to the repository's default `main` branch so judges can find it immediately.

Machine-readable browser results: `reports/cloud-deployment-verification.json`. Screenshots: `reports/cloud-live-desktop.png` and `reports/cloud-live-mobile.png`.
