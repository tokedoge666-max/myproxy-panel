import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Drawer, Empty, Skeleton } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { getApiError, singboxApi } from '../api/client';
import type { LogsResponse } from '../types';

function logsToText(payload: LogsResponse | string[]): string {
  if (Array.isArray(payload)) return payload.join('\n');
  if (Array.isArray(payload.lines)) return payload.lines.join('\n');
  if (Array.isArray(payload.logs)) return payload.logs.join('\n');
  if (typeof payload.logs === 'string') return payload.logs;
  return payload.content ?? '';
}

export function LogsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setContent(logsToText(await singboxApi.logs(200)));
    } catch (requestError) {
      setError(getApiError(requestError, '日志读取失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  return (
    <Drawer
      title={<div><strong>sing-box 日志</strong><span className="drawer-subtitle">最近 200 行 · 已过滤敏感字段</span></div>}
      width={760}
      open={open}
      onClose={onClose}
      className="logs-drawer"
      extra={<Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>刷新</Button>}
    >
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {loading && !content ? <Skeleton active paragraph={{ rows: 14 }} /> : null}
      {!loading && !error && !content ? <Empty description="暂无日志" /> : null}
      {content ? <pre className="log-viewer" aria-label="sing-box 日志内容">{content}</pre> : null}
    </Drawer>
  );
}
