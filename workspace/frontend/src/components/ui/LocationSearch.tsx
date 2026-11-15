import { Loader2, MapPin, Search } from 'lucide-react';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { GeocodeError, searchPlaces, type Place } from '../../lib/geocode';

interface Props {
  onSelect: (place: Place) => void;
  placeholder?: string;
  variant: 'home' | 'panel';
  autoFocus?: boolean;
  initialValue?: string;
}

/**
 * Accessible combobox (WAI-ARIA 1.2 pattern) with geocoded suggestions listed directly below the field.
 * Accepts place names or raw "lat, lon" coordinates.
 */
export default function LocationSearch({ onSelect, placeholder = 'Choose your location', variant, autoFocus, initialValue }: Props) {
  const [query, setQuery] = useState(initialValue ?? '');
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [status, setStatus] = useState<'idle' | 'loading' | 'error' | 'empty'>('idle');
  const [errorText, setErrorText] = useState('');
  const listId = useId();
  const statusId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const userTyped = useRef(false);

  useEffect(() => {
    if (!userTyped.current) return;
    const q = query.trim();
    if (q.length < 2) return; // cleared synchronously in onChange
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setStatus('loading');
      try {
        const places = await searchPlaces(q, controller.signal);
        setResults(places);
        setActive(places.length ? 0 : -1);
        setStatus(places.length ? 'idle' : 'empty');
        setOpen(true);
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        setResults([]);
        setStatus('error');
        setErrorText(err instanceof GeocodeError ? err.message : 'Place search failed.');
        setOpen(true);
      }
    }, 280);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [query]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const choose = (place: Place) => {
    userTyped.current = false;
    setQuery(place.detail && !place.id.startsWith('coord:') ? `${place.name}, ${place.detail}` : place.name);
    setOpen(false);
    setResults([]);
    setStatus('idle');
    onSelect(place);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (results.length ? (i + 1) % results.length : -1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : -1));
    } else if (e.key === 'Enter') {
      if (open && active >= 0 && results[active]) {
        e.preventDefault();
        choose(results[active]);
      }
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        setOpen(false);
      } else {
        setQuery('');
      }
    }
  };

  const showList = open && (results.length > 0 || status === 'empty' || status === 'error');
  const activeId = active >= 0 && results[active] ? `${listId}-opt-${active}` : undefined;

  return (
    <div ref={rootRef} className={`location-search location-search--${variant}`}>
      <div className="location-search__field">
        <Search className="location-search__icon" aria-hidden="true" size={variant === 'home' ? 16 : 18} />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label="Search for a location by place name or coordinates"
          aria-autocomplete="list"
          aria-expanded={showList}
          aria-controls={listId}
          aria-activedescendant={activeId}
          aria-describedby={statusId}
          autoComplete="off"
          spellCheck={false}
          placeholder={placeholder}
          value={query}
          autoFocus={autoFocus}
          onChange={(e) => {
            userTyped.current = true;
            setQuery(e.target.value);
            if (e.target.value.trim().length < 2) {
              setResults([]);
              setStatus('idle');
              setOpen(false);
            }
          }}
          onFocus={() => results.length && setOpen(true)}
          onKeyDown={onKeyDown}
        />
        {status === 'loading' && <Loader2 className="location-search__spinner" aria-hidden="true" size={16} />}
      </div>
      <span id={statusId} className="visually-hidden" aria-live="polite">
        {status === 'loading'
          ? 'Searching places…'
          : status === 'empty'
            ? 'No matching places.'
            : status === 'error'
              ? errorText
              : results.length
                ? `${results.length} suggestions available. Use the up and down arrows to choose.`
                : ''}
      </span>
      <ul id={listId} role="listbox" aria-label="Location suggestions" className="location-search__list" hidden={!showList}>
        {results.map((p, i) => (
          <li
            key={p.id}
            id={`${listId}-opt-${i}`}
            role="option"
            aria-selected={i === active}
            className={i === active ? 'is-active' : undefined}
            onMouseEnter={() => setActive(i)}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => choose(p)}
          >
            <MapPin aria-hidden="true" size={15} />
            <span className="location-search__name">{p.name}</span>
            {p.detail && <span className="location-search__detail">{p.detail}</span>}
          </li>
        ))}
        {status === 'empty' && (
          <li className="location-search__message" role="presentation">
            No matching places. Try another name or type coordinates such as “37.7, -119.5”.
          </li>
        )}
        {status === 'error' && (
          <li className="location-search__message location-search__message--error" role="presentation">
            {errorText} You can still type coordinates such as “37.7, -119.5”.
          </li>
        )}
      </ul>
    </div>
  );
}
