import { useId } from 'react';

/**
 * A small (i) that reveals explanatory text on hover or keyboard focus. The text is also the accessible
 * description, so nothing is hidden from screen readers.
 */
export default function InfoTip({ text, label = 'More information' }: { text: string; label?: string }) {
  const id = useId();
  return (
    <span className="info-tip">
      <button type="button" className="info-tip__button" aria-label={label} aria-describedby={id}>
        <span aria-hidden="true">i</span>
      </button>
      <span role="tooltip" id={id} className="info-tip__bubble">
        {text}
      </span>
    </span>
  );
}
