# Hosting the LogicLens landing page

The site is a **static** `index.html` (plus this file). Host it anywhere that serves static files.

## GitHub Pages

1. Push the `landing` folder on branch `main`.
2. Repository **Settings → Pages** → Source: branch `main`, folder **`/landing`** (or copy `index.html` into `/docs` and use `/docs`).
3. URLs in `index.html` are preset for **`shivin4/logiclens`**: installer download, releases, and source. Adjust the `canonical` and `og:url` meta tags if you use a **custom domain**.
4. After each release, bump **`RELEASE_TAG`**, **`INSTALLER_NAME`**, titles, and hero copy in `index.html` (keep them aligned with `logiclens/version.py` and `packaging/installer.iss`).

## Netlify / Vercel

- Set site root to the **`landing`** directory, or drag-and-drop that folder.
- No build step required.

## Linking downloads

Upload `LogicLens-Setup-1.1.0.exe` to the **v1.1.0** GitHub Release. The landing page expects this direct URL:

`https://github.com/shivin4/logiclens/releases/download/v1.1.0/LogicLens-Setup-1.1.0.exe`

For a quick “always latest” link during testing you can temporarily point buttons at:

`https://github.com/shivin4/logiclens/releases/latest/download/LogicLens-Setup-1.1.0.exe`

(use only if the uploaded asset name stays the same on every release).
