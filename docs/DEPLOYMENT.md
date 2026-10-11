# Deploying NEOS

NEOS has a Python API and a static web interface.

## Render

The repository's `render.yaml` defines the API service. Render installs `requirements.txt`, starts `uvicorn server:app` on its assigned port, and checks `/api/health`. The same service serves the web pages and their assets, including `/hub.html`, `/assets/*`, and `/static/*`.

## Vercel frontend with Render API

If the web folder is deployed separately as a Vercel static site, set the Vercel project's output/root directory to `web` and leave its build command empty. The page bundle routes API requests to the Render service at `https://neos-arpj.onrender.com` by default. If the API has another URL, set `localStorage.neosApiBase` to that API origin; the main console's API setting is shared with the participation page.

The API enables CORS for the static frontend. The participation demo uses a browser-generated session ID, and its state is held in the API process memory. It resets when that process restarts and is not a persistent account or a production demand-response service.

## OpenDSS

`opendssdirect.py` is installed by the declared Python requirements. The application still has its NumPy reference backend for environments where OpenDSS cannot initialize. The API's `/api/meta` response reports which grid backend was selected. All results remain simulations.
