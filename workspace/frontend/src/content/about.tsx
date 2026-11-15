import type { ReactNode } from 'react';

/** About-page tab content. Statements follow the backend documentation (FRONTEND_INTEGRATION.md, ML_INTEGRATION.md). */
export const ABOUT_TABS: { id: string; label: string; body: ReactNode }[] = [
  {
    id: 'overview',
    label: 'Overview',
    body: (
      <>
        <p>
          FireHarmonics is a web application for location-based analysis of satellite fire hotspot detections.
          Choose any place on Earth — by name, coordinates or a click on the globe — and FireHarmonics
          analyses the NASA FIRMS detections within a circle around it (25 km by default, 10–100 km
          selectable).
        </p>
        <p>
          Its core is one harmonized burning-activity calendar from 2002 to today that joins MODIS and VIIRS
          on a single scale. Around it the report shows current conditions, fire-season timing, long-term
          trends, unusual months, recurring hotspots and an experimental forecast — always with the data
          source, coverage and limitations next to the numbers.
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
          FireHarmonics was built for the NASA Space Apps Challenge 2026 challenge{' '}
          <em>Harmonization of MODIS and VIIRS Hot Spots</em>.
        </p>
        <p>
          MODIS (on Terra and Aqua, about 1 km pixels) and VIIRS (on Suomi NPP, NOAA-20 and NOAA-21, 375 m
          pixels) both detect active fires, but their counts are not directly comparable. The challenge asks
          how to bring the two records together into a consistent long-term view.
        </p>
        <p>
          FireHarmonics never adds MODIS and VIIRS counts. Instead it links the sensors in a continuity chain
          — Aqua MODIS, then Suomi NPP VIIRS, then NOAA-20 VIIRS — on the years each pair flew together,
          counts active 1-km cells instead of pixels, and treats day and night separately. The result is one
          calendar on the Aqua-MODIS scale for any place, with a 90 % interval for every month.
        </p>
        <p>
          We test it by switching MODIS off: fitted on 2012–2016, the chain predicts MODIS in 2017–2021 from
          VIIRS alone with a median annual error of 12–18 %, compared with 459–508 % for raw VIIRS. The method
          is experimental and labelled so.
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
          <strong>Choose.</strong> Search by name, type coordinates or click a country on the globe; the
          country lights up and the place is marked with a pin.
        </li>
        <li>
          <strong>Collect.</strong> Completed years come from NASA’s yearly archives of MODIS and VIIRS stored
          on our server; only the latest months are requested from the FIRMS API. An analysis can take up to
          ten minutes.
        </li>
        <li>
          <strong>Harmonize.</strong> The continuity chain puts Aqua MODIS, Suomi NPP VIIRS and NOAA-20 VIIRS
          on one scale and builds the 2002–now calendar, with uncertainty and unusual months flagged.
        </li>
        <li>
          <strong>Analyse.</strong> Seasonality and fire-season timing, Theil–Sen trends, robust anomalies and
          Getis-Ord Gi* hotspots are computed for the selected circle, plus current conditions from the last
          14 days.
        </li>
        <li>
          <strong>Forecast.</strong> A pre-registered validation gate chooses between pooled XGBoost and a
          seasonal baseline. In the installed model XGBoost passed for no region and lead, so the baseline is
          used and XGBoost is shown as an experimental comparison.
        </li>
        <li>
          <strong>Explore &amp; export.</strong> Open the report, switch data, area and period, and download
          PDF, CSV or JSON from the side panel or the report.
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
          Make satellite fire records understandable and honest. Every number in FireHarmonics comes from the
          analysis backend; missing data is shown as missing, never as zero.
        </p>
        <p>
          We separate what satellites observed from what models estimate, label experimental methods clearly,
          and state that a detection is a hot pixel seen from orbit — not a confirmed fire, burned area or
          ignition.
        </p>
      </>
    ),
  },
  {
    id: 'credits',
    label: 'Data & Credits',
    body: (
      <>
        <p>
          We acknowledge the use of data from NASA’s Fire Information for Resource Management System (FIRMS) (
          <a href="https://www.earthdata.nasa.gov/firms" target="_blank" rel="noreferrer">
            earthdata.nasa.gov/firms
          </a>
          ), part of NASA’s Earth Science Data and Information System (ESDIS). FireHarmonics uses the MODIS
          (Terra and Aqua) and VIIRS (Suomi NPP and NOAA-20) active-fire products, near real-time and standard
          processing.
        </p>
        <p>
          Earth imagery on the globe: NASA Visible Earth “Blue Marble”. Country borders: Natural Earth (public
          domain).
        </p>
        <p>
          FireHarmonics is a NASA Space Apps Challenge 2026 project. It is not affiliated with or endorsed by
          NASA, and its harmonization and forecast are the team’s own experimental methods.
        </p>
      </>
    ),
  },
];
