import { toPng } from 'html-to-image';
import { jsPDF } from 'jspdf';
import type { AnalysisResult } from '../api/types';
import { formatCoordinates } from './geocode';
import { saveBlob } from '../api/client';

/**
 * Client-side PDF of the Location Analysis Report: a text cover page built from the API result, followed by
 * an image of each rendered report section (charts included). No values are computed here.
 */
export async function exportReportPdf(root: HTMLElement, result: AnalysisResult): Promise<string> {
  const doc = new jsPDF({ unit: 'pt', format: 'a4', orientation: 'portrait' });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 36;
  const contentW = pageW - margin * 2;
  const loc = result.location;
  const name = loc.name ?? formatCoordinates(loc.latitude, loc.longitude);

  // cover
  doc.setFillColor(11, 15, 28);
  doc.rect(0, 0, pageW, pageH, 'F');
  doc.setTextColor(255, 122, 26);
  doc.setFontSize(11);
  doc.text('FireHarmonics · Location Analysis Report', margin, margin + 10);
  doc.setTextColor(240, 242, 248);
  doc.setFontSize(22);
  doc.text(doc.splitTextToSize(name, contentW), margin, margin + 46);
  doc.setFontSize(10.5);
  doc.setTextColor(190, 198, 214);
  const lines = [
    `Coordinates: ${formatCoordinates(loc.latitude, loc.longitude)} (WGS 84) · radius ${loc.radius_km} km`,
    `Descriptive source: ${result.source} via ${result.provenance?.provider ?? 'NASA FIRMS'} · coverage ${result.coverage.start_date ?? '—'} to ${result.coverage.end_date ?? '—'}`,
    `ML / forecast series: S-NPP VIIRS · ML status: ${result.ml_analysis.status}`,
    `Result status: ${result.status ?? '—'} · schema ${result.schema_version}`,
    `Forecast method selected by backend: ${result.prediction.method}`,
    `Generated in the browser on ${new Date().toISOString()} from the FireHarmonics API result.`,
    '',
    'Detections are satellite hotspot pixels, not individual fires. Forecasts give the probability of at least one',
    'satellite hotspot detection, not of a wildfire ignition. XGBoost values are experimental and not validated.',
  ];
  doc.text(lines, margin, margin + 96, { lineHeightFactor: 1.5 });

  const sections = [...root.querySelectorAll<HTMLElement>('[data-pdf-section]')];
  for (const section of sections) {
    const dataUrl = await toPng(section, {
      backgroundColor: '#0f1424',
      pixelRatio: 1.6,
      cacheBust: true,
      filter: (node) => !(node instanceof HTMLElement && node.dataset.pdfSkip !== undefined),
    });
    const img = await loadImage(dataUrl);
    const imgH = (img.height * contentW) / img.width;
    doc.addPage();
    doc.setFillColor(11, 15, 28);
    doc.rect(0, 0, pageW, pageH, 'F');
    const usable = pageH - margin * 2;
    if (imgH <= usable) {
      doc.addImage(dataUrl, 'PNG', margin, margin, contentW, imgH);
    } else {
      // tall section: repeat the image with a vertical offset on following pages (clipped by the page)
      let offset = 0;
      let first = true;
      while (offset < imgH) {
        if (!first) {
          doc.addPage();
          doc.setFillColor(11, 15, 28);
          doc.rect(0, 0, pageW, pageH, 'F');
        }
        doc.addImage(dataUrl, 'PNG', margin, margin - offset, contentW, imgH);
        offset += usable;
        first = false;
      }
    }
  }

  const safe = name.replace(/[^\w-]+/g, '-').replace(/-+/g, '-').slice(0, 60) || 'location';
  const filename = `fireharmonics-report-${safe}.pdf`;
  saveBlob(doc.output('blob'), filename);
  return filename;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not render a report section for the PDF.'));
    img.src = src;
  });
}
