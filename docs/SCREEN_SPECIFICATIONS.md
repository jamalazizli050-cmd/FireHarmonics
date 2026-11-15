# Approved five-page specification (for future implementation, NOT phase 0)

## General visual contract
- Faithful pixel-level layout to `design/mockups/01..05` and reuse of original illustration assets from `design/assets/`.
- FireHarmonics branding, dark navy star field, subtle glass effects. NASA-style scientific analytics inside expanded report; orange/red fire accents.
- Shared Home / About / Contact / FAQ navigation. Global accessibility panel: text sizing, high contrast, reduced motion, color-blind-friendly visualizations, keyboard accessibility.
- Responsive across desktop/tablet/mobile; preserve original desktop composition where possible.
- Avoid unrelated visual redesigns, placeholders, fake production statistics, or invented assets.

## 1 — Home (`01-home.jpeg`)
- Decorative photorealistic 3D Earth (no drag/zoom on Home), slow automated rotation; starfield subtle twinkling.
- On load: globe fade-in followed by title and search/navigation. Respect reduced-motion.
- Brand `FireHarmonics`, subtitle about harmonization.
- Static-position 'Choose your location' search over Earth, with suggestions directly **below** input (geocoded real locations, selectable).
- On selection: seamless shared-element-like transition in which Home Earth transforms/zooms into interactive globe page; target location receives a marker and summary. No hard cut if feasible.

## 2 — Interactive Globe (`02-interactive-globe.jpeg`)
- Globe left, rounded dark grey information panel right, same starfield/nav.
- Globe rotates/zooms via mouse/touch. Locations may **only be selected through search**, never by globe click.
- On selection, camera rotates to lat/lon. **Only a glowing pin**; no analysis radius overlay. Analysis radius fixed 25 km.
- Search at top of right panel. Summary metrics in right panel, plus expand action to overlay Location Analysis Report.
- Two-way synchronized selection of satellite source and time period in summary panel and expanded report.
- Source semantics: raw MODIS, raw VIIRS, or Harmonized where available. If UI includes `FIRMS`, clarify that it is provider, not sensor. Do not switch to scientifically incompatible aggregations silently.
- Overlay report (dark NASA-style dashboard, responsive, glassmorphism, orange/red accents) must contain:
  1. Header: location, coordinates, 25 km radius, actual coverage period.
  2. Overview: prediction probability, historical satellite detections, peak season, available years.
  3. Burning Activity Calendar: monthly-by-year heatmap; dark-grey through orange/red.
  4. Historical Trends: interactive line and bar time-series.
  5. AI Pattern Discovery: seasonal patterns, trends, anomalies, recurring geographic hotspots.
  6. AI Forecast: prominent **experimental XGBoost** output if backend exposes it; seasonal baseline side-by-side, target month, actual method, provenance, limitations.
  7. Satellite Analysis: MODIS vs VIIRS, real harmonization availability and status, coverage, scientific limitations.
  8. Export: actual PDF (front-end implemented in future), CSV/JSON (backend export endpoints if available).
- If XGBoost produces a value but backend marks `used:false` because not validated, visually foreground as **experimental comparison**, never falsely label as validated operational forecast. Don't overwrite fallback selection in backend.
- Always distinguish no data from zero, show loader/progress/SSE status, partial results, errors and missing values honestly.

## 3 — About (`03-about.jpeg`)
- Preserve layout: centered `What is FireHarmonics?` heading, large rounded central box, cloud/technology illustrations.
- Four tabs inside center box: Overview; NASA Challenge; How It Works; Our Mission. Smooth transitions and fade-in content.
- Separate `Meet the Team` cards **below** central block. Photo/avatar, name, role, GitHub/LinkedIn where provided. Team data eventually in separate config; don't invent names.

## 4 — Contact (`04-contact.jpeg`)
- Preserve original layout: white form left, contact-team illustration right, `Get in touch with our team` heading.
- Fields: first name, last name, email, optional phone, message.
- Validation after field touched, inline error states, submit progress, inline success or failure message with form contents preserved on failure.
- Send via external email provider (no custom contact backend), using appropriate public/private credential separation and provider-supported server-side rate limiting. Invisible honeypot. Never hardcode private mail credentials in client.

## 5 — FAQ (`05-faq.jpeg`)
- Preserve original heading, illustrated character and rounded tiles.
- 8–10 accurate questions about FireHarmonics/NASA/FIRMS/MODIS/VIIRS/harmonization/forecasting/exports.
- 2-column desktop grid, additional rows down the page; narrow screens one column. Multiple questions can be open concurrently; smooth height animation when expanding; reduced-motion fallback.

## Strictly no invented scientific data
Use only actual backend responses for metrics, predictions, availability and source details. Examples in documentation are not production fixtures. Never make up a fire probability for the demo.
