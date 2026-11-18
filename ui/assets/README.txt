Drop optional logo / hero files here (they ARE committed to git). The UI falls back to
built-in inline SVG artwork when a file is missing, so nothing breaks if you add none.

Recognised file names (png, svg, jpg, webp - keep the exact base name):
  mtm-logo.(svg|png)   event wordmark shown in the header
  gdg-logo.(svg|png)   organizer logo shown in the header strip
  hero.(svg|png)       replaces the illustration in the Plan tab hero panel

Sponsor / organizer logos referenced from ui/branding.json also live in this folder,
e.g. "logo": "assets/acme.png"   (served by the app at /assets/acme.png).
