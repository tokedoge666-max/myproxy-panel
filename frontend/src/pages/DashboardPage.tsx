import {
  ApiOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FieldTimeOutlined,
  FileTextOutlined,
  HddOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { Alert, App, Button, Card, Col, Progress, Row, Skeleton, Space, Tooltip } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { getApiError, nodeApi, singboxApi, systemApi } from '../api/client';
import { LogsDrawer } from '../components/LogsDrawer';
import { PageHeader } from '../components/PageHeader';
import { StatusPill } from '../components/StatusPill';
import type { ProxyNode, SystemStatus } from '../types';
import { formatBytes, formatProtocol, formatUptime, isRunning } from '../utils/format';

interface MetricCardProps {
  label: string;
  value: string;
  detail: string;
  percent?: number;
  icon: React.ReactNode;
  tone?: 'green' | 'blue' | 'amber';
}

function MetricCard({ label, value, detail, percent, icon, tone = 'green' }: MetricCardProps) {
  const stroke = tone === 'blue' ? '#5cc8ff' : tone === 'amber' ? '#f3bf63' : '#63e6be';
  return (
    <Card className="metric-card" bordered={false}>
      <div className={'metric-icon metric-icon--' + tone}>{icon}</div>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">{detail}</div>
      {typeof percent === 'number' ? (
        <Progress
          percent={Math.min(100, Math.max(0, percent))}
          showInfo={false}
          strokeColor={stroke}
          trailColor="#1a2731"
          size="small"
        />
      ) : null}
    </Card>
  );
}

function formatListenerState(node: ProxyNode): string {
  if (!node.listeners) return '监听状态未知';
  return Object.entries(node.listeners)
    .map(([transport, listening]) => {
      const label = transport.toUpperCase();
      return listening === null ? `${label} 未检测` : `${label} ${listening ? '已监听' : '未监听'}`;
    })
    .join(' · ');
}

export function DashboardPage() {
  const { message, modal } = App.useApp();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [nodes, setNodes] = useState<ProxyNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [logsOpen, setLogsOpen] = useState(false);

  const load = useCallback(async (manual = false) => {
    if (manual) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const [nextStatus, nextNodes] = await Promise.all([systemApi.status(), nodeApi.list()]);
      const runtimeStatus = new Map(
        (nextStatus.nodes ?? []).map((node) => [String(node.id), node]),
      );
      setStatus(nextStatus);
      setNodes(nextNodes.map((node) => {
        const runtime = runtimeStatus.get(String(node.id));
        return {
          ...node,
          status: runtime?.status ?? (node.enabled ? 'unknown' : 'disabled'),
          transport: runtime?.transport,
          listeners: runtime?.listeners,
        };
      }));
      setError('');
      if (manual) message.success('状态已刷新');
    } catch (requestError) {
      setError(getApiError(requestError, '系统状态读取失败'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const restart = () => {
    modal.confirm({
      title: '确认重启 sing-box？',
      content: '现有代理连接会短暂中断，服务状态将在重启后重新检查。',
      okText: '确认重启',
      cancelText: '取消',
      okButtonProps: { danger: true },
      async onOk() {
        try {
          await singboxApi.restart();
          message.success('重启指令已执行');
          await load();
        } catch (requestError) {
          message.error(getApiError(requestError, '重启失败'));
          throw requestError;
        }
      },
    });
  };

  const enabledNodes = useMemo(() => nodes.filter((node) => node.enabled), [nodes]);
  const firewallPorts = useMemo(() => enabledNodes.flatMap((node) => {
    if (node.protocol !== 'shadowsocks') return [`UDP ${node.listen_port}`];
    const config = node.config_json && typeof node.config_json === 'object' ? node.config_json : {};
    return config.udp === false
      ? [`TCP ${node.listen_port}`]
      : [`TCP ${node.listen_port}`, `UDP ${node.listen_port}`];
  }).join('、'), [enabledNodes]);
  const singboxRunning = isRunning(status?.singbox?.status);
  const hasEnabledNodes = enabledNodes.length > 0;
  const listenersHealthy = enabledNodes.every((node) => isRunning(node.status));
  const listenerChecksHealthy = !hasEnabledNodes
    || (status?.network?.listener_checks_available === true && listenersHealthy);
  const udpBuffers = status?.network?.udp_buffers;
  const udpBuffersHealthy = udpBuffers?.optimized === true;
  const controlHealthy = !error
    && singboxRunning
    && listenerChecksHealthy
    && udpBuffers?.optimized !== false;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="LIVE TELEMETRY"
        title="服务器运行总览"
        description="集中查看主机资源、代理核心与各协议节点的实时状态。"
        actions={
          <Space wrap>
            <Button icon={<FileTextOutlined />} onClick={() => setLogsOpen(true)}>查看日志</Button>
            <Button icon={<ReloadOutlined />} loading={refreshing} onClick={() => void load(true)}>刷新</Button>
            <Button danger onClick={restart}>重启 sing-box</Button>
          </Space>
        }
      />

      {error ? <Alert type="error" showIcon message="状态同步失败" description={error} action={<Button size="small" onClick={() => void load(true)}>重试</Button>} /> : null}

      {loading && !status ? (
        <Row gutter={[16, 16]}>{[0, 1, 2, 3].map((item) => <Col xs={24} sm={12} xl={6} key={item}><Card className="metric-card"><Skeleton active paragraph={{ rows: 2 }} /></Card></Col>)}</Row>
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} xl={6}>
            <MetricCard label="CPU LOAD" value={(status?.cpu_percent ?? 0).toFixed(1) + '%'} detail="当前处理器占用" percent={status?.cpu_percent} icon={<CloudServerOutlined />} />
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <MetricCard label="MEMORY" value={(status?.memory_used_mb ?? 0).toFixed(0) + ' MB'} detail={'共 ' + (status?.memory_total_mb ?? 0).toFixed(0) + ' MB'} percent={status?.memory_percent} icon={<DatabaseOutlined />} tone="blue" />
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <MetricCard label="DISK" value={(status?.disk_percent ?? 0).toFixed(1) + '%'} detail="系统磁盘占用" percent={status?.disk_percent} icon={<HddOutlined />} tone="amber" />
          </Col>
          <Col xs={24} sm={12} xl={6}>
            <MetricCard label="UPTIME" value={formatUptime(status?.uptime_seconds)} detail="服务器连续运行" icon={<FieldTimeOutlined />} />
          </Col>
        </Row>
      )}

      <section className="section-block">
        <div className="section-heading">
          <div><span>SERVICE MATRIX</span><h2>核心服务</h2></div>
          <span className="section-meta">15 秒自动更新</span>
        </div>
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={10}>
            <Card className={'service-hero ' + (singboxRunning ? 'service-hero--online' : 'service-hero--offline')} bordered={false}>
              <div className="service-hero-top">
                <div className="service-symbol"><ApiOutlined /></div>
                <StatusPill status={status?.singbox?.status} />
              </div>
              <h3>sing-box Core</h3>
              <p>承载所有代理协议的数据平面</p>
              <div className="service-facts">
                <div><span>版本</span><strong>{status?.singbox?.version || '未知'}</strong></div>
                <div><span>已启用节点</span><strong>{enabledNodes.length}</strong></div>
              </div>
            </Card>
          </Col>
          <Col xs={24} lg={14}>
            <Card className="node-status-card" bordered={false}>
              <div className="node-status-list">
                {nodes.length ? nodes.map((node) => (
                  <div className="node-status-row" key={String(node.id)}>
                    <div className="node-protocol-mark">{node.protocol === 'hysteria2' ? 'H2' : node.protocol === 'tuic' ? 'T5' : 'SS'}</div>
                    <div className="node-status-copy">
                      <strong>{node.name}</strong>
                      <span>{formatProtocol(node.protocol)} · {node.listen_port} · {formatListenerState(node)}</span>
                    </div>
                    <StatusPill
                      status={node.enabled ? node.status : 'disabled'}
                      activeLabel="运行中"
                      inactiveLabel={node.enabled ? '状态异常' : '已停用'}
                      warningLabel="监听异常"
                    />
                  </div>
                )) : (
                  <div className="empty-inline">暂无节点数据</div>
                )}
              </div>
            </Card>
          </Col>
        </Row>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div><span>CONNECTION HEALTH</span><h2>转发稳定性</h2></div>
          <span className="section-meta">本机只读检测</span>
        </div>
        <Card className="network-health-card" bordered={false}>
          <div className="network-diagnostic-grid">
            <div>
              <span>端口监听</span>
              <strong>{!hasEnabledNodes ? '暂无启用节点' : status?.network?.listener_checks_available ? (listenersHealthy ? '全部正常' : '存在异常') : '无法检测'}</strong>
            </div>
            <div>
              <span>UDP 接收缓冲上限</span>
              <strong>{formatBytes(udpBuffers?.receive_max_bytes ?? undefined)}</strong>
            </div>
            <div>
              <span>UDP 发送缓冲上限</span>
              <strong>{formatBytes(udpBuffers?.send_max_bytes ?? undefined)}</strong>
            </div>
            <div>
              <span>建议最低值</span>
              <strong>{formatBytes(udpBuffers?.recommended_min_bytes)}</strong>
            </div>
          </div>
          <Alert
            type={!hasEnabledNodes ? 'info' : !udpBuffersHealthy || !listenerChecksHealthy ? 'warning' : 'success'}
            showIcon
            message={!hasEnabledNodes ? '当前没有启用的代理节点' : udpBuffers?.optimized === false ? 'UDP 缓冲偏低，QUIC 高流量时可能丢包' : udpBuffers?.optimized == null ? '无法读取 UDP 缓冲状态' : listenerChecksHealthy ? '本机数据面检查通过' : '有节点未检测到预期监听端口'}
            description={(
              <span>
                这里验证 sing-box 进程、本机 IPv4 端口与 UDP 缓冲；云安全组和客户端到服务器的公网链路仍需在 Clash Verge Rev 中实测。
                {firewallPorts ? <><br />当前需在 UFW 与云安全组放行：{firewallPorts}。</> : null}
              </span>
            )}
          />
        </Card>
      </section>

      <Card className="system-note" bordered={false}>
        <div><span className="secure-dot" /><div><strong>{controlHealthy ? '控制面与数据面状态正常' : '检测到需要处理的运行项'}</strong><p>{controlHealthy ? 'API 由 Nginx 安全转发，所有已启用节点均检测到预期监听。' : '请先查看上方 UDP 缓冲、端口状态与 sing-box 日志，再检查防火墙和云安全组。'}</p></div></div>
        <Tooltip title="详细状态仅对已登录管理员可见"><span>{controlHealthy ? 'PRIVATE ENDPOINT' : 'ATTENTION'}</span></Tooltip>
      </Card>

      <LogsDrawer open={logsOpen} onClose={() => setLogsOpen(false)} />
    </div>
  );
}
