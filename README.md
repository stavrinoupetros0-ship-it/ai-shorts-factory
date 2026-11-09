# AI Shorts Factory (phone-managed starter)

This repository is a cloud-run starter for generating original narrated Shorts from AI-written stories and AI-generated still images with cinematic motion.

## What it does
- Generates 5 short scripts with Gemini.
- Generates one original image per story with Cloudflare Workers AI FLUX.
- Creates narration with Microsoft Edge TTS (free-to-use service, but not an SLA-backed API).
- Renders 9:16 MP4s with FFmpeg, animated zoom and subtitles.
- Optionally uploads videos to YouTube as **private**.

It does not create fully generative text-to-video motion. The animation is a cinematic pan/zoom on original AI images. The first version deliberately defaults to render-only so you can inspect the output before enabling uploads.

## Phone-only setup

You can manage this repository from Safari on iPhone. No local installation is required.

### 1. Create a public GitHub repository
1. Visit https://github.com/signup
2. Verify your email.
3. Create a repository named `ai-shorts-factory`.
4. Choose **Public** (standard GitHub-hosted Actions runners are free for public repositories).
5. Do not add a README/license/gitignore when creating it; upload the contents of this ZIP.

### 2. Add the files
Open the repository on GitHub in Safari:
1. Tap **Add file → Upload files**.
2. Extract this ZIP first using the iPhone Files app.
3. Upload the contents of the `ai-shorts-factory` folder, keeping the folder structure.
4. Commit the files to the `main` branch.

### 3. Get a Gemini API key
1. Visit https://aistudio.google.com/apikey
2. Create an API key.
3. In GitHub repository: Settings → Secrets and variables → Actions → New repository secret.
4. Name it `GEMINI_API_KEY`.

Gemini free-tier availability and model access can change. The default model is `gemini-2.5-flash`; change `GEMINI_MODEL` in the workflow if your account does not have access.

### 4. Get a Cloudflare Workers AI token
1. Create an account at https://dash.cloudflare.com/sign-up
2. Find your Account ID in the Workers & Pages overview.
3. Create an API token with permission to use Workers AI.
4. Add GitHub Actions secrets:
   - `CF_ACCOUNT_ID`
   - `CF_API_TOKEN`

The image model configured is `@cf/black-forest-labs/flux-1-schnell`. Workers AI free allocation is limited and model usage can exhaust it; the workflow generates only one image per video.

### 5. Test generation before connecting YouTube
1. In GitHub, open Actions → **Generate YouTube Shorts**.
2. Tap **Run workflow**.
3. Keep `upload_to_youtube` set to `false`.
4. Run it and wait for completion.
5. Download the `shorts-output` artifact from the completed run. Open the MP4s in Files to review.

### 6. Connect YouTube (only after you like the videos)
You need a Google Cloud project and OAuth credentials:
1. Visit https://console.cloud.google.com/
2. Create a project and enable **YouTube Data API v3**.
3. Configure the OAuth consent screen. Add your own Google account as a test user if the app is in Testing.
4. Create an OAuth client ID. A Desktop app client is suitable for a one-time local/device authorization flow; do not publish its client secret.
5. Obtain a refresh token for the `https://www.googleapis.com/auth/youtube.upload` scope using Google's OAuth Playground or a trusted OAuth flow configured with your own client. Use offline access and keep the refresh token private.
6. Add these GitHub Actions secrets:
   - `YT_CLIENT_ID`
   - `YT_CLIENT_SECRET` (if your OAuth client type uses one)
   - `YT_REFRESH_TOKEN`

Never paste secrets into chat, source files, issues, screenshots, or a public repository.

**YouTube API caution:** API projects that have not passed YouTube's required audit may have uploads restricted to private visibility. This workflow intentionally uploads privately. Check your project's current quota and API compliance status before enabling automated uploads.

### 7. Enable uploads carefully
After you have tested the videos and configured YouTube secrets:
1. Go to Settings → Secrets and variables → Actions → Variables.
2. Add repository variable `ENABLE_UPLOADS` with value `true`.
3. Run the workflow manually once with `upload_to_youtube=true`.
4. Confirm the uploaded videos appear in YouTube Studio as private.
5. Only later change `privacyStatus` in `src/upload_youtube.py` if your API project and channel are eligible for public publishing.

The daily scheduled run is at 06:00 UTC (08:00 Cyprus time during winter; 09:00 during summer). GitHub scheduled runs can be delayed during busy periods.

## Free-tier reality
- GitHub Actions: standard hosted runner usage is free for public repositories, subject to GitHub policies and limits.
- Cloudflare Workers AI: free allocation is limited (currently 10,000 Neurons/day on the Workers Free plan; model costs vary).
- Gemini API: free tier limits, eligible models and data-use terms vary by region and can change.
- Edge TTS: uses an online service and is not a guaranteed permanent free API.
- YouTube Data API: quota and verification restrictions apply.
- “Free” does not mean unlimited. Five daily image generations may exceed a free AI allocation depending on model cost and current limits.

## Monetization and originality
AI-assisted content is not automatically disqualified from monetization. Avoid repetitive mass-produced videos, reused footage, fabricated claims and low-value templated content. Add original research, narration and meaningful editorial value. Fact-check claims and retain source URLs in descriptions.

## Troubleshooting
- `429` from Gemini or Cloudflare: free-tier rate/usage limit; retry later or reduce `VIDEOS_PER_RUN`.
- Image model error: check Cloudflare token permissions, account ID and model availability.
- Edge TTS error: rerun later; service availability can vary.
- OAuth error: check refresh token, client ID, consent screen and YouTube Data API enablement.
- Uploads remain private: this can be expected for an unverified API project.
