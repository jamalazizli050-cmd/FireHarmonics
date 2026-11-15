/** FAQ entries. Facts follow the backend documentation (FRONTEND_INTEGRATION.md, docs/ML_INTEGRATION.md). */
export const FAQ: { q: string; a: string[] }[] = [
  {
    q: 'Do I need to create an account to use the platform?',
    a: [
      'No. FireHarmonics has no sign-up or login. Search for a place, type coordinates or click a country on the globe, and the analysis starts.',
    ],
  },
  {
    q: 'Where does the fire data come from?',
    a: [
      'From NASA FIRMS (Fire Information for Resource Management System). FIRMS is the distribution service, not a satellite sensor.',
      'For the selected period you can choose VIIRS on NOAA-20 or MODIS on Terra/Aqua (near real-time). The 2002–now calendar uses NASA’s yearly archives of Aqua MODIS, Suomi NPP VIIRS and NOAA-20 VIIRS, and the history and forecast use the Suomi NPP VIIRS record.',
    ],
  },
  {
    q: 'What does “harmonized” mean here?',
    a: [
      'Aqua MODIS and VIIRS put on one consistent scale (the Aqua-MODIS scale), so the burning-activity record from 2002 to today does not jump when the satellites change. Choose “Harmonized · MODIS + VIIRS” in Satellite data to see it; the full calendar is in the report.',
      'It is our own method, labelled experimental, and validated on held-out years (see the next question). The normalized index in Satellite Analysis is different: it only rescales one sensor and is not a harmonization.',
    ],
  },
  {
    q: 'How do MODIS and VIIRS differ, and how are they combined into one calendar?',
    a: [
      'MODIS detects fires in roughly 1 km pixels, VIIRS in 375 m pixels and sees smaller fires, so their raw counts are not comparable and are never simply added together.',
      'FireHarmonics uses a continuity chain: Aqua MODIS before 2012, S-NPP VIIRS from 2012 converted to the Aqua-MODIS scale, and NOAA-20 VIIRS converted through S-NPP after S-NPP stopped delivering data. Each pair of satellites is linked on the years both flew.',
      'Activity is counted as active 1-km cells per day or night overpass, so a fire that VIIRS splits into many small pixels is not counted many times. Day and night get separate conversion factors, and where a place has little fire history the factor leans on the surrounding region.',
      'To test it we “switch MODIS off”: factors fitted on 2012–2016 predict MODIS in 2017–2021 from VIIRS alone. Across our test locations the median annual error was 12–18 %, against 459–508 % for raw VIIRS, and the true value fell inside the 90 % interval in about 90 % of months. Each report shows this test for its own location.',
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
      'A circle around the chosen point: 25 km radius by default, or 10, 50 or 100 km. When you click a country on the globe, the circle is centred on the point you clicked, not on the whole country.',
      'The forecast model was trained on circles of exactly these sizes, and results are only comparable between places analysed with the same radius.',
    ],
  },
  {
    q: 'How long does an analysis take?',
    a: [
      'Up to ten minutes. Completed years come from NASA’s yearly archives stored on our server, and only the most recent months are requested from the FIRMS API. Close to NASA’s request limit the backend waits until the limit frees up, usually a minute or two; the panel shows each step live.',
      'Near-real-time products start only a few months back, so a long period chosen with them is shortened to the dates NASA has; the 2002–now calendar and the history are not affected. In a country whose archives are not on the server yet, the calendar is prepared in the background and appears when you analyse the place again a few minutes later.',
    ],
  },
  {
    q: 'Can I export the results?',
    a: [
      'Yes, from the side panel or the report: a PDF of the report generated in your browser, the full result as JSON, and individual tables as CSV — the selected period (daily, monthly, annual, detection cells), the harmonized MODIS+VIIRS calendar, and the ML monthly, seasonal, anomaly and hotspot tables when the ML stage ran.',
    ],
  },
];
