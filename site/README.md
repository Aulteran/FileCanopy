# FileCanopy website

The public product site. Plain HTML and CSS, no build step, no framework, and one tiny optional script (`assets/lightbox.js`) for click-to-enlarge screens; everything works without it. It is independent of the app and its Docker stack.

- `index.html`: the single page.
- `assets/`: stylesheet, logo, self-hosted fonts (SIL OFL, licences included) and screenshots.
- `assets/shots/`: **design previews** rendered from the approved mockups, not the real app. Replace them with real screenshots when the redesigned UI ships (keep the file names, or edit `index.html`).

All links to its own files are relative, and there is no hardcoded domain, so the folder works from a domain root or from a sub-path.

## Preview locally

```bash
python3 -m http.server 8000 --directory site
```

## Hosting

**GitHub Pages (current).** `.github/workflows/site.yml` publishes `site/` whenever it changes on `main`. One-time setup: repository **Settings > Pages > Source: GitHub Actions**. The site appears at `https://<owner>.github.io/FileCanopy/`.

**Cloudflare Pages.** Connect the repository, set the build command to empty and the output directory to `site`.

**Your own domain or a subdomain.** Upload the contents of `site/` to any static host (or point a subdomain at Cloudflare Pages or GitHub Pages and add a `CNAME`). Nothing in the files needs to change. For GitHub Pages with a custom domain, add the domain under Settings > Pages.
