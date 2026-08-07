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
import { formatProtocol, formatUptime, isRunning } from '../utils/format';

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
        (nextStatus.nodes ?? []).map((node) => [String(node.id), node.status]),
      );
      setStatus(nextStatus);
      setNodes(nextNodes.map((node) => ({
        ...node,
        status: runtimeStatus.get(String(node.id)) ?? (node.enabled ? 'unknown' : 'disabled'),
      })));
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
  const singboxRunning = isRunning(status?.singbox?.status);

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
                      <span>{formatProtocol(node.protocol)} · {node.listen_port}/{node.protocol === 'shadowsocks' ? 'TCP+UDP' : 'UDP'}</span>
                    </div>
                    <StatusPill
                      status={node.enabled ? node.status : 'disabled'}
                      activeLabel="运行中"
                      inactiveLabel={node.enabled ? '状态异常' : '已停用'}
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

      <Card className="system-note" bordered={false}>
        <div><span className="secure-dot" /><div><strong>控制面状态正常</strong><p>API 通过本机回环地址提供服务，公网请求由 Nginx 安全转发。</p></div></div>
        <Tooltip title="详细状态仅对已登录管理员可见"><span>PRIVATE ENDPOINT</span></Tooltip>
      </Card>

      <LogsDrawer open={logsOpen} onClose={() => setLogsOpen(false)} />
    </div>
  );
}
