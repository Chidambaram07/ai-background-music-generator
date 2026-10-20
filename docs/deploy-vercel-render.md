# Free Temporary Demo: Vercel And Render

This configuration is a disposable preview, not an assessment-grade persistent deployment. It provisions no paid disk, database, S3 service or AI model. S3 is not needed for this preview, but the assessment's S3-compatible-storage integration point still requires a verified S3/MinIO deployment.

Proposed names, subject to availability:

- Frontend project: `incubrix-music-engine`, normally `https://incubrix-music-engine.vercel.app`.
- Backend service: `incubrix-music-api`; use the actual `https://...onrender.com` address Render assigns.

These are requested names, not reserved or verified live URLs. If either platform assigns a suffix, use the actual URL in the environment settings below. No custom-domain purchase is required.

## 1. Render Backend First

1. Sign in to https://dashboard.render.com/ using your own account.
2. Choose **New > Blueprint** and connect `Chidambaram07/ai-background-music-generator`, branch `main`.
3. Render reads `render.yaml` at the repository root. Confirm the only service is `incubrix-music-api`, Docker runtime, **Free** compute plan, with no persistent disk/database.
4. Approve the free service creation. If the dashboard requests a paid resource, stop and review rather than upgrading automatically.
5. Wait for the image build and `/api/v1/ready` health check. Record the actual assigned backend URL.
6. Open `<actual-backend-url>/api/v1/health`, `<actual-backend-url>/api/v1/ready`, and `<actual-backend-url>/docs`.

If creating manually, use the repository root, Dockerfile `Dockerfile.backend`, health path `/api/v1/ready`, one worker and the environment settings in `render.yaml`. The Docker command invokes Uvicorn directly on port10000, matching the explicit `PORT=10000` setting; it does not depend on shell quoting or variable expansion. Do not launch a separate worker instance; this application currently owns its worker in the web process.

The free configuration sets `AUDIO_PROVIDER=procedural`, `LLM_PROVIDER=mock`, and `STORAGE_BACKEND=local`. No Hugging Face, MusicGen, paid LLM or S3 key is required. SQLite and generated audio live under `/tmp/incubrix` and can disappear when the service sleeps, restarts or redeploys.

## 2. Vercel Frontend

1. Sign in to https://vercel.com/ and choose **Add New > Project**.
2. Import the same GitHub repository and leave Root Directory at the repository root.
3. Set Project Name to `incubrix-music-engine`, if available.
4. Select Node.js **24.x** in the project runtime settings. `vercel.json` selects Vite, `npm ci`, `npm run build` and output `dist`.
5. Add **Production** environment variable `VITE_API_BASE_URL` with the actual Render backend URL, without a trailing slash. This is a public URL, not a secret.
6. Deploy. Vite embeds this value during the build; changing it requires redeploying the frontend.
7. Record the actual frontend Production URL. In Render, set `FRONTEND_ORIGINS` to exactly that HTTPS origin and redeploy the backend if it differs from the proposed name.

Do not use `localhost` as the deployed frontend's API URL. Preview deployments have different origins and are not automatically allowed by the backend's exact CORS list. For the first check, use the Vercel Production URL, not a random preview address.

## 3. Check The Demo

- Verify frontend loading and backend readiness from the public URLs, not localhost.
- Try a **5-10 second** track first; verify polling, completion, playback, stem downloads and the manifest.
- Free Render currently provides very limited CPU/RAM. Long tracks, concurrent submissions and large uploads may be slow, fail or exceed memory. Successful local 45-300s tests do not prove the same capacity on a free cloud instance.
- Render spins down free services after inactivity; cold start can take about a minute. The frontend's existing request timeout can expire during that start. Open the readiness URL first, wait for a healthy response and then retry.
- Save any demo output you need before the instance restarts. Lost jobs/audio are expected in this explicitly ephemeral preview, not evidence of durable storage.
- This deployment has no added application login or comprehensive abuse-rate limits. Keep it a temporary, limited-review demo; CORS is not authentication and does not prevent direct API abuse.

## What This Does Not Establish

No public deployment, paid-resource approval, always-on availability, durable evidence storage, live AI generation, commercial licence verification or assessment PASS is claimed by these config files. The optional MusicGen research model is not installed or used. Keep the submission's persistence/availability/S3 limitations until those are genuinely resolved and tested.

Official references: https://render.com/docs/blueprint-spec ; https://render.com/docs/free ; https://vercel.com/docs/project-configuration . Free tiers can have usage limits and possible overage/suspension rules; review the dashboards before enabling deployment.