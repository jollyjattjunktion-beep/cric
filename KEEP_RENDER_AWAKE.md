# Keep the Preet Sports Broadcaster Running

The dashboard tab is not the owner of the match poller. The broadcast pipeline runs in `server.py`; closing/minimizing the dashboard does not send a stop request. However, Render Free web services spin down after 15 minutes without inbound traffic, which can terminate in-memory broadcast state.

## What was added

- `GET /api/health` provides a small health response.
- `.github/workflows/keep-render-awake.yml` calls that endpoint every 5 minutes from GitHub Actions.
- `render.yaml` uses `/api/health` for the `cricket-ai-streamer` health check.

The workflow targets `https://cric-he5z.onrender.com` (the current service URL from the project setup). If your Render service URL changes, update `RENDER_BASE_URL` in the workflow.

## Enable it

1. Push/deploy the latest `main` branch to Render so `/api/health` is available.
2. In GitHub, open **Actions** for this repository. If prompted, enable workflows.
3. Open **Keep Render Cricket Broadcaster Awake** and use **Run workflow** once to test it. Scheduled runs then continue every 5 minutes.

## Important Free-plan trade-off

The scheduled ping is inbound traffic and is intended to prevent idle spin-down even when no browser is open. This also keeps the Free web service active when you are not broadcasting. Render's free instance-hour allowance is shared across services in the workspace; a continuously active service uses roughly 720 hours in a 30-day month. Disable the workflow when you do not need always-on behavior, or use a paid always-on service for more reliable broadcasting. GitHub scheduled runs and Render instances can still be delayed or restarted.

## Audio behavior

- With a YouTube Live stream key supplied in the dashboard before starting, the server starts its separate YouTube streaming engine, so the browser is not the audio output device.
- Without a stream key, commentary audio is delivered to the dashboard's browser player. A closed browser cannot produce sound through the user's speakers, even if the server continues generating commentary.
- The broadcaster stores active-match state in memory. If Render restarts the instance for a reason other than idle spin-down, you may need to start the broadcast again.
