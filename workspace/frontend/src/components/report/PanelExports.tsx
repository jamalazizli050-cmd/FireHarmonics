import { Download, Loader2 } from 'lucide-react';
import { useId, useState } from 'react';
import { ApiError, downloadExport } from '../../api/client';
import { CSV_TABLES, type AnalysisResult, type CsvTable } from '../../api/types';
import { ML_TABLES, TABLE_LABELS } from './sections/ModelSections';

/**
 * Downloads in the side panel. JSON and CSV come straight from the backend export endpoint; the PDF is rendered
 * from the report, so that button opens the report and starts the PDF there.
 */
export default function PanelExports({
  result,
  jobId,
  onPdf,
}: {
  result: AnalysisResult;
  jobId: string | null;
  onPdf: () => void;
}) {
  const id = useId();
  const mlRan = ['ok', 'partial', 'insufficient_data'].includes(
    result.components?.ml_analysis ?? result.ml_analysis.status,
  );
  const [table, setTable] = useState<CsvTable>('monthly');
  const [busy, setBusy] = useState<'json' | 'csv' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (kind: 'json' | 'csv') => {
    if (!jobId) return;
    setBusy(kind);
    setError(null);
    try {
      await (kind === 'json' ? downloadExport(jobId, 'json') : downloadExport(jobId, 'csv', table));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel-exports" aria-labelledby={`${id}-h`} data-testid="panel-exports">
      <h2 id={`${id}-h`} className="panel-exports__title">
        Download
      </h2>
      <div className="panel-exports__row">
        <button type="button" className="panel-exports__button" onClick={onPdf}>
          <Download aria-hidden="true" size={14} /> PDF
        </button>
        <button
          type="button"
          className="panel-exports__button"
          disabled={!jobId || busy !== null}
          onClick={() => void run('json')}
        >
          {busy === 'json' ? (
            <Loader2 className="spin" size={14} aria-hidden="true" />
          ) : (
            <Download aria-hidden="true" size={14} />
          )}{' '}
          JSON
        </button>
      </div>
      <div className="panel-exports__row">
        <label className="visually-hidden" htmlFor={`${id}-csv`}>
          CSV table
        </label>
        <select id={`${id}-csv`} value={table} onChange={(e) => setTable(e.target.value as CsvTable)}>
          {CSV_TABLES.map((t) => (
            <option key={t} value={t} disabled={ML_TABLES.has(t) && !mlRan}>
              {TABLE_LABELS[t]}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="panel-exports__button"
          disabled={!jobId || busy !== null}
          onClick={() => void run('csv')}
        >
          {busy === 'csv' ? (
            <Loader2 className="spin" size={14} aria-hidden="true" />
          ) : (
            <Download aria-hidden="true" size={14} />
          )}{' '}
          CSV
        </button>
      </div>
      {!jobId && (
        <p className="panel-exports__note">JSON and CSV are available when the current analysis completes.</p>
      )}
      {error && (
        <p className="panel-exports__note panel-exports__note--error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
