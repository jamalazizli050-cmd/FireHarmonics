/** FAQ entries. Facts follow the backend documentation (FRONTEND_INTEGRATION.md, docs/ML_INTEGRATION.md). */
export const FAQ: { q: string; a: string[] }[] = [
  {
    q: 'Do I need to create an account to use the platform?',
    a: ['No. FireHarmonics has no sign-up or login. Search for a location and the analysis starts.'],
  },
  {
    q: 'Where does the fire data come from?',
    a: [
      'From NASA FIRMS (Fire Information for Resource Management System). FIRMS is the distribution service, not a satellite sensor.',
      'The detections come from the MODIS instruments on Terra and Aqua, the VIIRS instruments on Suomi NPP, NOAA-20 and NOAA-21, and (US and Canada only) Landsat OLI.',
    ],
  },
  {
    q: 'What does “harmonized” mean here?',
    a: [
      'A MODIS-equivalent monthly series derived from VIIRS for 2012–2021. It exists only for five training regions (California, Zambia/Angola, Rondônia, Yakutia and Punjab) and covers only fires both sensors observed.',
      'Counts are validated in California and Zambia/Angola only, fire radiative power (FRP) is not fully validated in any region, and nothing is validated at the local (10–100 km) scale. Elsewhere FireHarmonics offers a separate, locally calibrated MODIS+VIIRS calendar (see below).',
    ],
  },
  {
    q: 'How do MODIS and VIIRS differ, and how are they combined into one calendar?',
    a: [
      'MODIS detects fires in roughly 1 km pixels, VIIRS in 375 m pixels and sees smaller fires, so their raw counts are not comparable and are never simply added together.',
      'FireHarmonics builds a 2002–present calendar for the selected circle: Aqua MODIS before 2012, and S-NPP VIIRS from 2012 converted to the Aqua-MODIS scale. Both satellites pass over at the same local time.',
      'The conversion factor is estimated for that place on 2012–2021, when both sensors observed, and checked year by year (leave-one-year-out) against MODIS. The report shows the factor, its 95 % interval and the validation result. It is a local calibration, not the FireLens regional model, and is labelled experimental.',
    ],
  },
  {
    q: 'Is a “detection” the same as a fire?',
    a: [
      'No. A detection is a satellite pixel flagged as unusually hot during an overpass. One fire can produce many detections, and clouds, smoke or tree canopy can hide fires completely.',
      'Detection counts are not fire counts, burned area or ignitions.',
    ],
  },
  {
    q: 'What exactly does the forecast predict?',
    a: [
      'The probability of at least one usable S-NPP VIIRS vegetation-fire detection within the selected circle (25 km by default) during the target month.',
      'It is a satellite-detection probability, not the probability that a wildfire will start. When no forecast can be issued the value is shown as “not issued”, never as 0 %.',
    ],
  },
  {
    q: 'Why is the XGBoost forecast labelled experimental?',
    a: [
      'The backend only uses pooled XGBoost where it beat the seasonal baseline in a pre-registered held-out test. In the installed model it passed for no region and no lead time, so the seasonal baseline is the forecast actually selected.',
      'FireHarmonics still shows the XGBoost value prominently, marked “Experimental · Not validated · May be inaccurate”, next to the baseline for comparison.',
    ],
  },
  {
    q: 'How large is the analysed area?',
    a: [
      'A circle around the chosen point: 25 km radius by default, or 10, 50 or 100 km. The forecast model was trained on circles of exactly these sizes.',
      'Results are only comparable between places analysed with the same radius.',
    ],
  },
  {
    q: 'Why can an analysis take a long time?',
    a: [
      'Completed years come from NASA’s yearly per-country archives, and only the current year is requested piece by piece from the FIRMS API (in parallel, within NASA’s transaction limit). A new location usually takes a few minutes; a NASA rate-limit pause can add about 10 minutes. The panel shows live progress.',
      'Locations analysed before, and other places in a country already downloaded, are served from the local cache in seconds.',
    ],
  },
  {
    q: 'Can I export the results?',
    a: [
      'Yes. The report offers a PDF generated in your browser, the full result as JSON, and individual tables as CSV (daily, monthly, annual and hotspot tables, plus the ML monthly, seasonal, anomaly and hotspot tables when the ML stage ran).',
    ],
  },
];
