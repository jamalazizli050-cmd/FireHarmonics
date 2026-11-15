import type { ReactNode } from 'react';

/** About-page tab content. Statements follow the backend documentation (FRONTEND_INTEGRATION.md, ML_INTEGRATION.md). */
export const ABOUT_TABS: { id: string; label: string; body: ReactNode }[] = [
  {
    id: 'overview',
    label: 'Overview',
    body: (
      <>
        <p>
          FireHarmonics is a web application for location-based analysis of satellite fire hotspot detections. Choose any
          place on Earth and FireHarmonics analyses the NASA FIRMS detections within a circle around it (25 km by default, 10–100 km selectable).
        </p>
        <p>
          The report shows when and how often hotspots were detected, how activity changed over the years, which months
          and grid cells stand out, and an experimental estimate for the coming months — always with the data source,
          coverage and limitations next to the numbers.
        </p>
      </>
    ),
  },
  {
    id: 'challenge',
    label: 'NASA Challenge',
    body: (
      <>
        <p>
          FireHarmonics was built for the NASA Space Apps Challenge 2026 challenge <em>Harmonization of MODIS and VIIRS
          Hot Spots</em>.
        </p>
        <p>
          MODIS (on Terra and Aqua, about 1 km pixels) and VIIRS (on Suomi NPP, NOAA-20 and NOAA-21, 375 m pixels) both
          detect active fires, but their counts are not directly comparable. The challenge asks how to bring the two
          records together into a consistent long-term view.
        </p>
        <p>
          FireHarmonics keeps raw MODIS and raw VIIRS separate and never adds their counts. A harmonized,
          MODIS-equivalent series exists only for five training regions, and its validation status is shown wherever it
          is used.
        </p>
      </>
    ),
  },
  {
    id: 'how',
    label: 'How It Works',
    body: (
      <ol className="steps">
        <li>
          <strong>Search.</strong> Pick a place by name or coordinates; the globe turns to it and marks it with a pin.
        </li>
        <li>
          <strong>Download.</strong> The backend requests detections from NASA FIRMS for your chosen product and period,
          plus a 10-year S-NPP VIIRS history for the ML analysis. Completed years come from NASA’s yearly per-country archives, so a new location usually takes a few minutes;
          saved ones load in seconds.
        </li>
        <li>
          <strong>Analyse.</strong> Descriptive statistics, seasonality, Theil–Sen trends, robust anomalies, Isolation
          Forest cell-months and Getis-Ord Gi* hotspots are computed for the selected circle.
        </li>
        <li>
          <strong>Forecast.</strong> A pre-registered validation gate chooses between pooled XGBoost and a seasonal
          baseline. In the installed model XGBoost passed for no region and lead, so the baseline is used and XGBoost is
          shown as an experimental comparison.
        </li>
        <li>
          <strong>Explore &amp; export.</strong> Open the report, switch sources and periods, and export PDF, CSV or JSON.
        </li>
      </ol>
    ),
  },
  {
    id: 'mission',
    label: 'Our Mission',
    body: (
      <>
        <p>
          Make satellite fire records understandable and honest. Every number in FireHarmonics comes from the analysis
          backend; missing data is shown as missing, never as zero.
        </p>
        <p>
          We separate what satellites observed from what models estimate, label experimental methods clearly, and state
          that a detection is a hot pixel seen from orbit — not a confirmed fire, burned area or ignition.
        </p>
      </>
    ),
  },
];
