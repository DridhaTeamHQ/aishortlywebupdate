'use client';

import { useEffect, useRef, useState } from 'react';

export type CategoryOption = { value: string; label: string; icon: string };

type Props = {
  options: CategoryOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
};

export default function CategoryDropdown({ options, value, onChange, disabled, id }: Props) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const current = options.find((o) => o.value === value) || options[0];

  return (
    <div className="cdd" ref={wrapRef} id={id}>
      <button
        type="button"
        className={`cdd-trigger${open ? ' is-open' : ''}`}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="cdd-icon">{current?.icon}</span>
        <span className="cdd-label">{current?.label}</span>
        <span className="cdd-chevron" aria-hidden>▾</span>
      </button>

      {open && (
        <ul className="cdd-menu" role="listbox">
          {options.map((opt) => {
            const selected = opt.value === value;
            return (
              <li
                key={opt.value}
                role="option"
                aria-selected={selected}
                className={`cdd-option${selected ? ' is-selected' : ''}`}
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
              >
                <span className="cdd-icon">{opt.icon}</span>
                <span className="cdd-label">{opt.label}</span>
                {selected && <span className="cdd-check" aria-hidden>✓</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
