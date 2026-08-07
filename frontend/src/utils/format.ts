import type { ProxyNode, ProxyProtocol } from '../types';

export const protocolLabels: Record<ProxyProtocol, string> = {
  hysteria2: 'Hysteria2',
  tuic: 'TUIC',
  shadowsocks: 'Shadowsocks 2022',
};

export function formatProtocol(protocol: string): string {
  return protocolLabels[protocol as ProxyProtocol] ?? protocol;
}

export function formatUptime(totalSeconds?: number | null): string {
  if (totalSeconds === undefined || totalSeconds === null || totalSeconds < 0) return '—';
  const seconds = Math.floor(totalSeconds);
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  if (days > 0) return days + '天 ' + hours + '小时';
  if (hours > 0) return hours + '小时 ' + minutes + '分钟';
  return Math.max(1, minutes) + '分钟';
}

export function formatBytes(bytes?: number): string {
  if (bytes === undefined || Number.isNaN(bytes)) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 ** 2).toFixed(1) + ' MB';
}

export function getNodeConfig(node?: ProxyNode | null): Record<string, unknown> {
  if (!node?.config_json) return {};
  if (typeof node.config_json === 'object') return node.config_json;
  try {
    const parsed = JSON.parse(node.config_json) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function isRunning(status?: string | null): boolean {
  return ['active', 'running', 'enabled', 'online', 'ok'].includes((status ?? '').toLowerCase());
}

export function displayDate(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
