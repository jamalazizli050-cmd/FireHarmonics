import { Accessibility, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { TEXT_SCALES, useA11y, type MotionPref } from '../../state/a11y';

/** Site-wide accessibility settings (floating button + non-modal dialog). */
export default function AccessibilityPanel() {
  const a11y = useA11y();
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>('button, input, select')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const scaleIndex = TEXT_SCALES.indexOf(a11y.textScale as (typeof TEXT_SCALES)[number]);

  return (
    <div className="a11y">
      <button
        ref={buttonRef}
        type="button"
        className="a11y__toggle"
        aria-expanded={open}
        aria-controls="a11y-panel"
        onClick={() => setOpen((o) => !o)}
      >
        <Accessibility aria-hidden="true" size={22} />
        <span className="visually-hidden">Accessibility settings</span>
      </button>
      {open && (
        <div id="a11y-panel" ref={panelRef} className="a11y__panel glass" role="dialog" aria-labelledby={titleId}>
          <div className="a11y__head">
            <h2 id={titleId}>Accessibility</h2>
            <button
              type="button"
              className="icon-button"
              onClick={() => {
                setOpen(false);
                buttonRef.current?.focus();
              }}
            >
              <X aria-hidden="true" size={18} />
              <span className="visually-hidden">Close accessibility settings</span>
            </button>
          </div>

          <fieldset className="a11y__group">
            <legend>Text size</legend>
            <div className="a11y__stepper">
              <button
                type="button"
                className="chip"
                disabled={scaleIndex <= 0}
                onClick={() => a11y.update({ textScale: TEXT_SCALES[Math.max(0, scaleIndex - 1)] })}
              >
                A−<span className="visually-hidden"> smaller text</span>
              </button>
              <output aria-live="polite">{Math.round(a11y.textScale * 100)} %</output>
              <button
                type="button"
                className="chip"
                disabled={scaleIndex >= TEXT_SCALES.length - 1}
                onClick={() =>
                  a11y.update({ textScale: TEXT_SCALES[Math.min(TEXT_SCALES.length - 1, scaleIndex + 1)] })
                }
              >
                A+<span className="visually-hidden"> larger text</span>
              </button>
            </div>
          </fieldset>

          <label className="switch">
            <input
              type="checkbox"
              checked={a11y.highContrast}
              onChange={(e) => a11y.update({ highContrast: e.target.checked })}
            />
            <span>High contrast</span>
          </label>

          <label className="switch">
            <input
              type="checkbox"
              checked={a11y.colorBlind}
              onChange={(e) => a11y.update({ colorBlind: e.target.checked })}
            />
            <span>Colour-blind-friendly charts</span>
          </label>

          <fieldset className="a11y__group">
            <legend>Motion</legend>
            {(
              [
                ['system', 'Follow system setting'],
                ['reduce', 'Reduce motion'],
                ['full', 'Full motion'],
              ] as [MotionPref, string][]
            ).map(([value, label]) => (
              <label key={value} className="radio">
                <input
                  type="radio"
                  name="a11y-motion"
                  value={value}
                  checked={a11y.motion === value}
                  onChange={() => a11y.update({ motion: value })}
                />
                <span>{label}</span>
              </label>
            ))}
            <p className="a11y__hint">
              Currently: {a11y.reduceMotion ? 'animations reduced' : 'animations on'}
            </p>
          </fieldset>

          <button type="button" className="link-button" onClick={a11y.reset}>
            Reset to defaults
          </button>
        </div>
      )}
    </div>
  );
}
