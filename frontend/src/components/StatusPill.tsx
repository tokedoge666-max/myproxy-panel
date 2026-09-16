import { Badge } from 'antd';
import { isRunning } from '../utils/format';

interface StatusPillProps {
  status?: string | null;
  activeLabel?: string;
  inactiveLabel?: string;
  warningLabel?: string;
}

export function StatusPill({
  status,
  activeLabel = '运行中',
  inactiveLabel = '已停止',
  warningLabel = '需检查',
}: StatusPillProps) {
  const normalized = (status ?? '').toLowerCase();
  const active = isRunning(status);
  const warning = ['degraded', 'unknown'].includes(normalized);
  const label = active ? activeLabel : warning ? warningLabel : inactiveLabel;
  return (
    <span className={'status-pill ' + (active ? 'status-pill--active' : warning ? 'status-pill--warning' : 'status-pill--inactive')}>
      <Badge status={active ? 'success' : warning ? 'warning' : 'error'} />
      {label}
    </span>
  );
}
