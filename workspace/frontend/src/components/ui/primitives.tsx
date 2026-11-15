import { AlertTriangle, Info, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';

export function Badge({
  tone = 'neutral',
  children,
  title,
}: {
  tone?: 'neutral' | 'ok' | 'warn' | 'danger' | 'fire' | 'info';
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Notice({
  tone = 'info',
  title,
  children,
  action,
  role,
}: {
  tone?: 'info' | 'warn' | 'error';
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  role?: 'alert' | 'status';
}) {
  const Icon = tone === 'error' ? XCircle : tone === 'warn' ? AlertTriangle : Info;
  return (
    <div className={`notice notice--${tone}`} role={role}>
      <Icon aria-hidden="true" size={18} className="notice__icon" />
      <div className="notice__body">
        {title && <p className="notice__title">{title}</p>}
        {children && <div className="notice__text">{children}</div>}
        {action && <div className="notice__action">{action}</div>}
      </div>
    </div>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} aria-label={label}>
      <div className="progress__fill" style={{ width: `${v}%` }} />
    </div>
  );
}

export function Metric({
  label,
  value,
  sub,
  tone,
  testId,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'fire' | 'default';
  testId?: string;
}) {
  return (
    <div className={`metric ${tone === 'fire' ? 'metric--fire' : ''}`} data-testid={testId}>
      <dt className="metric__label">{label}</dt>
      <dd className="metric__value">{value}</dd>
      {sub && <dd className="metric__sub">{sub}</dd>}
    </div>
  );
}

export function StatusText({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge>unknown</Badge>;
  const tone =
    status === 'ok' || status === 'complete' || status === 'available' || status === 'validated'
      ? 'ok'
      : status === 'partial' || status === 'regional_only' || status === 'insufficient_data' || status.includes('only')
        ? 'warn'
        : status === 'failed' || status === 'unavailable' || status === 'not_validated'
          ? 'danger'
          : 'neutral';
  return <Badge tone={tone}>{status.replace(/_/g, ' ')}</Badge>;
}
