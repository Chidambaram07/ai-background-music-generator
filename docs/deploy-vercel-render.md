# Free Temporary Demo: Vercel And Render

This configuration is a disposable preview, not an assessment-grade persistent deployment. It provisions no paid disk, database, S3 service or AI model. S3 is not needed for this preview, but the assessment's S3-compatible-storage integration point still requires a verified S3/MinIO deployment.

Assigned demo URLs (public HTTP access verified on 2026-10-20):

- Frontend: https://incubrix-music-engine.vercel.app
- Backend: https://incubrix-music-api.onrender.com
- Backend readiness: https://incubrix-music-api.onrender.com/api/v1/ready

These are free platform subdomains, not purchased custom domains. The backend readiness and health endpoints returned HTTP 200. A live 45-second procedural job succeeded and its mix, four stems and manifest were accessible; this does not establish uninterrupted availability or subjective music quality. If recreating the services and either platform assigns a suffix, use the new actual URLs in the environment settings below.

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

If dashboard import does not load, the official Vercel CLI can create/link the same project and deploy it. Use Node 24, the repository root, the saved Production API URL above, and the free default build machine. Connect the existing GitHub repository for automatic deployments; do not create a second repository or enable paid build options.

For local CLI deployments, `.vercelignore` allows only frontend source, public assets and build configuration. Do not rely on `.gitignore` alone: a dry run included local test evidence before the explicit allowlist was added. Run `vercel deploy --dry --json` and verify that no `.env` files, `.vercel` metadata, backend data, generated audio or `test_results` are uploaded. Keep CLI-generated authentication files local and ignored.

## 3. Check The Demo

- Verify frontend loading and backend readiness from the public URLs, not localhost.
- Try a **5-10 second** track first; verify polling, completion, playback, stem downloads and the manifest.
- Free Render currently provides very limited CPU/RAM. Long tracks, concurrent submissions and large uploads may be slow, fail or exceed memory. Successful local 45-300s tests do not prove the same capacity on a free cloud instance.
- Render spins down free services after inactivity; cold start can take about a minute. The frontend's existing request timeout can expire during that start. Open the readiness URL first, wait for a healthy response and then retry.
- A lost connection during status polling can leave the page showing the last known state even if the backend later succeeds. Check `/api/v1/jobs/<job-id>` directly before treating a stale progress display as a failed render. Reloading clears the frontend's in-memory active job; automatic reconnect/resume is not established by this deployment.
- Save any demo output you need before the instance restarts. Lost jobs/audio are expected in this explicitly ephemeral preview, not evidence of durable storage.
- This deployment has no added application login or comprehensive abuse-rate limits. Keep it a temporary, limited-review demo; CORS is not authentication and does not prevent direct API abuse.

## What This Does Not Establish

No paid-resource approval, always-on availability, durable evidence storage, live AI generation, commercial licence verification or assessment PASS is established by this demo. The optional MusicGen research model is not installed or used. Keep the submission's persistence/availability/S3 limitations until those are genuinely resolved and tested.

Official references: https://render.com/docs/blueprint-spec ; https://render.com/docs/free ; https://vercel.com/docs/project-configuration . Free tiers can have usage limits and possible overage/suspension rules; review the dashboards before enabling deployment.