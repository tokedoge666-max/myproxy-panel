import { Badge } from 'antd';
import { isRunning } from '../utils/format';

interface StatusPillProps {
  status?: string | null;
  activeLabel?: string;
  inactiveLabel?: string;
}

export function StatusPill({
  status,
  activeLabel = '运行中',
  inactiveLabel = '已停止',
}: StatusPillProps) {
  const active = isRunning(status);
  return (
    <span className={'status-pill ' + (active ? 'status-pill--active' : 'status-pill--inactive')}>
      <Badge status={active ? 'success' : 'error'} />
      {active ? activeLabel : inactiveLabel}
    </span>
  );
}
