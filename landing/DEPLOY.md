# Hosting the LogicLens landing page

The site is a **static** `index.html` (plus this file). Host it anywhere that serves static files.

## GitHub Pages

1. Push the `landing` folder (or copy `index.html` to `docs/`).
2. Repository **Settings → Pages** → Source: branch `main`, folder `/landing` or `/docs`.
3. Set **INSTALLER_URL** and **SOURCE_URL** in `index.html` to your real GitHub Releases link and repo URL.
4. Optional: use a custom domain in Pages settings.

## Netlify / Vercel

- Drag-and-drop the `landing` directory, or connect the repo with root `landing`.
- No build step required.

## Linking downloads

Upload `LogicLens-Setup-x.y.z.exe` to **GitHub Releases** and use a **direct asset URL**, for example:

`https://github.com/ORG/REPO/releases/download/v1.0.0/LogicLens-Setup-1.0.0.exe`

Paste that into the `INSTALLER_URL` constant in `index.html`.
