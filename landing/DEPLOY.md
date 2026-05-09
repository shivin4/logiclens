# Hosting the LogicLens landing page

The site is a **static** `index.html` (plus this file). Host it anywhere that serves static files.

## GitHub Pages

Use this when the site files live in **`landing/`** on **`main`** (already pushed).

1. Open **Settings → Pages** for the repo:  
   `https://github.com/shivin4/logiclens/settings/pages`
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
3. **Branch**: `main` / **`/landing`** (the `/landing` folder option in the second dropdown), then **Save**.
4. Wait one to three minutes. The site URL is:  
   **`https://shivin4.github.io/logiclens/`**  
   If you see 404, hard-refresh or wait for the first build to finish; confirm **Pages** shows a green “last deployed” state.
5. **`index.html`** is already wired for **`shivin4/logiclens`**: installer URL, releases, and source links. **`canonical`** and **`og:url`** point at the GitHub Pages URL above; change both only if you add a **custom domain** (to that domain’s `https://…` URL).
6. **Naming**: user-facing copy uses **LogicLens 1.1.0** (release title style). The Git **tag** for downloads stays **`v1.1.0`** — that value is `RELEASE_TAG` in the `<script>` block and must match the tag on the GitHub Release.
7. After each release, bump **`RELEASE_TAG`**, **`INSTALLER_NAME`**, titles, and hero copy in `landing/index.html` (keep them aligned with `logiclens/version.py` and `packaging/installer.iss`).

**Alternative:** copy `landing/index.html` into **`docs/`** on `main`, then in Pages choose branch `main` / folder **`/docs`**.

## Netlify / Vercel

- Set site root to the **`landing`** directory, or drag-and-drop that folder.
- No build step required.

## Linking downloads

Upload `LogicLens-Setup-1.1.0.exe` to the **v1.1.0** GitHub Release. The landing page expects this direct URL:

`https://github.com/shivin4/logiclens/releases/download/v1.1.0/LogicLens-Setup-1.1.0.exe`

For a quick “always latest” link during testing you can temporarily point buttons at:

`https://github.com/shivin4/logiclens/releases/latest/download/LogicLens-Setup-1.1.0.exe`

(use only if the uploaded asset name stays the same on every release).
