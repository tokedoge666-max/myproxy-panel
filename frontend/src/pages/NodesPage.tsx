import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { Alert, App, Button, Card, Col, Empty, Row, Skeleton, Space, Switch, Tag } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { getApiError, nodeApi } from '../api/client';
import { NodeEditorDrawer } from '../components/NodeEditorDrawer';
import { PageHeader } from '../components/PageHeader';
import type { NodePayload, ProxyNode } from '../types';
import { displayDate, formatProtocol } from '../utils/format';

const protocolClass: Record<string, string> = {
  hysteria2: 'protocol-mark--hy2',
  tuic: 'protocol-mark--tuic',
  shadowsocks: 'protocol-mark--ss',
};

const protocolShort: Record<string, string> = {
  hysteria2: 'H2',
  tuic: 'T5',
  shadowsocks: 'SS',
};

export function NodesPage() {
  const { message, modal } = App.useApp();
  const [nodes, setNodes] = useState<ProxyNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingNode, setEditingNode] = useState<ProxyNode | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingId, setPendingId] = useState<ProxyNode['id'] | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setNodes(await nodeApi.list());
      setError('');
    } catch (requestError) {
      setError(getApiError(requestError, '节点列表读取失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openCreate = () => {
    setEditingNode(null);
    setEditorOpen(true);
  };

  const openEdit = (node: ProxyNode) => {
    setEditingNode(node);
    setEditorOpen(true);
  };

  const save = async (payload: NodePayload) => {
    setSaving(true);
    try {
      if (editingNode) await nodeApi.update(editingNode.id, payload);
      else await nodeApi.create(payload);
      message.success(editingNode ? '节点配置已安全应用' : '节点已创建');
      setEditorOpen(false);
      await load();
    } catch (requestError) {
      message.error(getApiError(requestError, '节点保存失败'));
      throw requestError;
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (node: ProxyNode, enabled: boolean) => {
    const execute = async () => {
      setPendingId(node.id);
      try {
        await nodeApi.setEnabled(node.id, enabled);
        message.success(node.name + (enabled ? ' 已启用' : ' 已停用'));
        await load();
      } catch (requestError) {
        message.error(getApiError(requestError, '节点状态修改失败'));
      } finally {
        setPendingId(null);
      }
    };

    if (enabled) {
      await execute();
      return;
    }

    modal.confirm({
      title: '停用 ' + node.name + '？',
      content: '保存后该节点会从 sing-box 配置与订阅中移除，现有连接将中断。',
      okText: '确认停用',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: execute,
    });
  };

  const regenerate = (node: ProxyNode) => {
    modal.confirm({
      title: '重生 ' + node.name + ' 的凭证？',
      content: '旧凭证将立即失效。你需要在所有客户端重新更新订阅。',
      okText: '重生凭证',
      cancelText: '取消',
      okButtonProps: { danger: true },
      async onOk() {
        setPendingId(node.id);
        try {
          await nodeApi.regenerateSecret(node.id);
          message.success('新凭证已生成并安全应用');
          await load();
        } catch (requestError) {
          message.error(getApiError(requestError, '凭证重生失败'));
          throw requestError;
        } finally {
          setPendingId(null);
        }
      },
    });
  };

  const remove = (node: ProxyNode) => {
    modal.confirm({
      title: '删除 ' + node.name + '？',
      content: '节点会从配置和订阅中永久移除，此操作无法撤销。',
      okText: '永久删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      async onOk() {
        try {
          await nodeApi.remove(node.id);
          message.success('节点已删除');
          await load();
        } catch (requestError) {
          message.error(getApiError(requestError, '节点删除失败'));
          throw requestError;
        }
      },
    });
  };

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="INGRESS NODES"
        title="代理节点"
        description="维护 Hysteria2、TUIC v5 与 Shadowsocks 2022 入站节点。"
        actions={<Space wrap><Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button><Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>创建节点</Button></Space>}
      />

      {error ? <Alert type="error" showIcon message="节点同步失败" description={error} /> : null}

      {loading && !nodes.length ? (
        <Row gutter={[16, 16]}>{[0, 1, 2].map((item) => <Col xs={24} xl={8} key={item}><Card className="node-card"><Skeleton active paragraph={{ rows: 6 }} /></Card></Col>)}</Row>
      ) : nodes.length ? (
        <Row gutter={[16, 16]}>
          {nodes.map((node) => (
            <Col xs={24} md={12} xl={8} key={String(node.id)}>
              <Card className={'node-card ' + (node.enabled ? 'node-card--enabled' : 'node-card--disabled')} bordered={false}>
                <div className="node-card-head">
                  <div className={'protocol-mark ' + protocolClass[node.protocol]}>{protocolShort[node.protocol] ?? 'PX'}</div>
                  <div className="node-title"><strong>{node.name}</strong><span>{formatProtocol(node.protocol)}</span></div>
                  <Switch
                    size="small"
                    checked={node.enabled}
                    loading={pendingId === node.id}
                    aria-label={(node.enabled ? '停用 ' : '启用 ') + node.name}
                    onChange={(checked) => void toggle(node, checked)}
                  />
                </div>
                <div className="node-endpoint">
                  <span>LISTEN PORT</span>
                  <strong>{node.listen_port}</strong>
                  <Tag bordered={false}>{node.protocol === 'shadowsocks' ? 'TCP + UDP' : 'UDP'}</Tag>
                </div>
                <div className="node-properties">
                  <div><span>状态</span><strong className={node.enabled ? 'text-success' : 'text-muted'}>{node.enabled ? '● 已启用' : '○ 已停用'}</strong></div>
                  <div><span>凭证</span><strong>••••••••••••</strong></div>
                  <div><span>最近更新</span><strong>{displayDate(node.updated_at)}</strong></div>
                </div>
                <div className="node-card-actions">
                  <Button icon={<EditOutlined />} onClick={() => openEdit(node)}>编辑</Button>
                  <Button icon={<KeyOutlined />} onClick={() => regenerate(node)}>重生凭证</Button>
                  <Button type="text" danger icon={<DeleteOutlined />} aria-label={'删除 ' + node.name} onClick={() => remove(node)} />
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      ) : (
        <Card className="empty-card"><Empty image={<ApiOutlined className="empty-icon" />} description="尚未创建代理节点"><Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>创建第一个节点</Button></Empty></Card>
      )}

      <Card className="security-strip" bordered={false}>
        <KeyOutlined />
        <div><strong>凭证不会在列表中明文显示</strong><span>所有随机密码、UUID 与 SS2022 密钥均由服务端安全生成；日志和审计记录只保留脱敏信息。</span></div>
      </Card>

      <NodeEditorDrawer
        open={editorOpen}
        node={editingNode}
        saving={saving}
        onClose={() => !saving && setEditorOpen(false)}
        onSave={save}
      />
    </div>
  );
}
