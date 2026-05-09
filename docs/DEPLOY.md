# Hosting the LogicLens landing page

The site is **`docs/index.html`** (static). Host it anywhere that serves static files.

## GitHub Pages (recommended)

**Important:** “Deploy from a branch” only supports folder **`/ (root)`** or **`/docs`** — not `/landing`. This repo keeps the site under **`docs/`** so you can pick **`/docs`** in the UI.

Configure in the **repository** (not your GitHub account profile):  
`https://github.com/shivin4/logiclens/settings/pages`

1. **Source:** **Deploy from a branch**
2. **Branch:** `main` → **`/docs`** → **Save**
3. After a minute or two, the site is **`https://shivin4.github.io/logiclens/`** (GitHub serves `docs/index.html` as the site root — you do not get `/docs` in the public URL).

**`index.html`** is wired for **`shivin4/logiclens`**: installer, releases, source. Update **`canonical`** and **`og:url`** only if you use a **custom domain**.

**Naming:** User-facing copy uses **LogicLens 1.1.0**. The Git **tag** for downloads is **`v1.1.0`** (`RELEASE_TAG` in the script) and must match the GitHub Release tag.

After each release, bump **`RELEASE_TAG`**, **`INSTALLER_NAME`**, titles, and hero copy in **`docs/index.html`** (align with `logiclens/version.py` and `packaging/installer.iss`).

## Netlify / Vercel

- Site root = **`docs`** (or upload the `docs` folder).

## Linking downloads

Upload `LogicLens-Setup-1.1.0.exe` to the **v1.1.0** GitHub Release. Expected URL:

`https://github.com/shivin4/logiclens/releases/download/v1.1.0/LogicLens-Setup-1.1.0.exe`

For testing “latest” only (if the asset name never changes):

`https://github.com/shivin4/logiclens/releases/latest/download/LogicLens-Setup-1.1.0.exe`
