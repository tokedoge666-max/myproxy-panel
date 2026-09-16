import { InfoCircleOutlined, LockOutlined } from '@ant-design/icons';
import { Alert, Button, Drawer, Form, Input, InputNumber, Select, Space, Switch } from 'antd';
import { useEffect } from 'react';
import type { NodePayload, ProxyNode, ProxyProtocol } from '../types';
import { getNodeConfig, protocolLabels } from '../utils/format';

interface NodeFormValues {
  name: string;
  protocol: ProxyProtocol;
  listen_port: number;
  enabled: boolean;
  up_mbps?: number;
  down_mbps?: number;
  congestion_control?: string;
  heartbeat?: string;
  method?: string;
  udp?: boolean;
}

interface NodeEditorDrawerProps {
  open: boolean;
  node: ProxyNode | null;
  saving: boolean;
  onClose: () => void;
  onSave: (payload: NodePayload) => Promise<void>;
}

function initialValues(node: ProxyNode | null): NodeFormValues {
  const config = getNodeConfig(node);
  return {
    name: node?.name ?? 'New Node',
    protocol: node?.protocol ?? 'hysteria2',
    listen_port: node?.listen_port ?? 8443,
    enabled: node?.enabled ?? true,
    up_mbps: typeof config.up_mbps === 'number' ? config.up_mbps : undefined,
    down_mbps: typeof config.down_mbps === 'number' ? config.down_mbps : undefined,
    congestion_control: typeof config.congestion_control === 'string' ? config.congestion_control : 'cubic',
    heartbeat: typeof config.heartbeat === 'string' ? config.heartbeat : '10s',
    method:
      typeof config.method === 'string'
        ? config.method
        : typeof config.cipher === 'string'
          ? config.cipher
          : '2022-blake3-aes-128-gcm',
    udp: typeof config.udp === 'boolean' ? config.udp : true,
  };
}

export function NodeEditorDrawer({ open, node, saving, onClose, onSave }: NodeEditorDrawerProps) {
  const [form] = Form.useForm<NodeFormValues>();
  const protocol = Form.useWatch('protocol', form) ?? node?.protocol ?? 'hysteria2';

  useEffect(() => {
    if (open) form.setFieldsValue(initialValues(node));
  }, [form, node, open]);

  const submit = async (values: NodeFormValues) => {
    const existing = getNodeConfig(node);
    let config: Record<string, unknown>;

    if (values.protocol === 'hysteria2') {
      const existingObfs = existing.obfs && typeof existing.obfs === 'object'
        ? (existing.obfs as Record<string, unknown>)
        : {};
      config = {
        ...existing,
        obfs: { ...existingObfs, type: 'salamander' },
        // Keep explicit nulls so clearing an existing optional limit reaches the backend.
        up_mbps: values.up_mbps ?? null,
        down_mbps: values.down_mbps ?? null,
        ignore_client_bandwidth: values.up_mbps == null && values.down_mbps == null,
      };
    } else if (values.protocol === 'tuic') {
      const stableExisting = { ...existing };
      delete stableExisting.version;
      delete stableExisting.reduce_rtt;
      config = {
        ...stableExisting,
        congestion_control: values.congestion_control ?? 'cubic',
        zero_rtt_handshake: false,
        heartbeat: values.heartbeat ?? '10s',
      };
    } else {
      config = {
        ...existing,
        method: values.method ?? '2022-blake3-aes-128-gcm',
        udp: values.udp ?? true,
      };
    }

    await onSave({
      name: values.name.trim(),
      protocol: values.protocol,
      enabled: values.enabled,
      listen_port: values.listen_port,
      config_json: config,
    });
  };

  return (
    <Drawer
      title={<div><strong>{node ? '编辑节点' : '创建节点'}</strong><span className="drawer-subtitle">保存前由后端生成并校验配置</span></div>}
      width={560}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" loading={saving} onClick={() => form.submit()}>保存节点</Button>
        </Space>
      }
    >
      <Alert
        className="form-alert"
        type="info"
        showIcon
        icon={<InfoCircleOutlined />}
        message="安全应用"
        description="节点修改会经过 staging、sing-box check 和健康检查；失败时由服务端自动回滚。"
      />
      <Form<NodeFormValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => void submit(values)}
      >
        <div className="form-grid form-grid--two">
          <Form.Item name="name" label="节点名称" rules={[{ required: true, message: '请输入节点名称' }, { max: 64, message: '最多 64 个字符' }]}>
            <Input placeholder="例如 LA-HY2" />
          </Form.Item>
          <Form.Item name="protocol" label="代理协议" rules={[{ required: true }]}>
            <Select
              disabled={Boolean(node)}
              options={(Object.keys(protocolLabels) as ProxyProtocol[]).map((key) => ({ value: key, label: protocolLabels[key] }))}
              onChange={(value: ProxyProtocol) => {
                const port = value === 'hysteria2' ? 8443 : value === 'tuic' ? 10443 : 8388;
                form.setFieldValue('listen_port', port);
              }}
            />
          </Form.Item>
          <Form.Item
            name="listen_port"
            label="监听端口"
            rules={[{ required: true, message: '请输入端口' }, { type: 'number', min: 1024, max: 65535, message: '请输入 1024–65535 之间的端口' }]}
          >
            <InputNumber min={1024} max={65535} precision={0} controls={false} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="enabled" label="启用节点" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
        </div>

        <div className="form-section-title"><span>PROTOCOL</span><strong>{protocolLabels[protocol]}</strong></div>
        {protocol === 'hysteria2' ? (
          <div className="form-grid form-grid--two">
            <Form.Item label="混淆方式">
              <Input value="salamander" disabled />
            </Form.Item>
            <Form.Item label="TLS">
              <Input value="使用系统证书" disabled />
            </Form.Item>
            <Form.Item
              name="up_mbps"
              label="Brutal 服务端上行声明 Mbps"
              extra="仅在客户端也声明带宽时生效；面板订阅默认使用自适应 BBR，一般保持留空。"
              dependencies={['down_mbps']}
              rules={[({ getFieldValue }) => ({
                validator(_, value) {
                  return (value == null) === (getFieldValue('down_mbps') == null)
                    ? Promise.resolve()
                    : Promise.reject(new Error('上下行限速需同时填写或同时留空'));
                },
              })]}
            >
              <InputNumber min={1} precision={0} controls={false} placeholder="不限制" style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="down_mbps"
              label="Brutal 服务端下行声明 Mbps"
              dependencies={['up_mbps']}
              rules={[({ getFieldValue }) => ({
                validator(_, value) {
                  return (value == null) === (getFieldValue('up_mbps') == null)
                    ? Promise.resolve()
                    : Promise.reject(new Error('上下行限速需同时填写或同时留空'));
                },
              })]}
            >
              <InputNumber min={1} precision={0} controls={false} placeholder="不限制" style={{ width: '100%' }} />
            </Form.Item>
          </div>
        ) : null}

        {protocol === 'tuic' ? (
          <div className="form-grid form-grid--two">
            <Form.Item label="协议版本"><Input value="TUIC v5" disabled /></Form.Item>
            <Form.Item name="congestion_control" label="拥塞控制">
              <Select options={[
                { value: 'cubic', label: 'CUBIC（兼容优先）' },
                { value: 'bbr', label: 'BBR' },
                { value: 'new_reno', label: 'New Reno' },
              ]} />
            </Form.Item>
            <Form.Item name="heartbeat" label="心跳间隔" rules={[{ pattern: /^[1-9]\d*(ms|s|m)$/, message: '例如 10s 或 500ms' }]}>
              <Input placeholder="10s" />
            </Form.Item>
            <Form.Item label="0-RTT / Reduce RTT"><Input value="关闭（安全默认值）" disabled /></Form.Item>
          </div>
        ) : null}

        {protocol === 'shadowsocks' ? (
          <div className="form-grid form-grid--two">
            <Form.Item name="method" label="加密方法">
              <Select options={[
                { value: '2022-blake3-aes-128-gcm', label: '2022-blake3-aes-128-gcm' },
                { value: '2022-blake3-aes-256-gcm', label: '2022-blake3-aes-256-gcm' },
              ]} />
            </Form.Item>
            <Form.Item name="udp" label="UDP 转发" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          </div>
        ) : null}

        <div className="credential-note"><LockOutlined /><div><strong>凭证由系统安全托管</strong><span>密码与 UUID 不在编辑表单中回显。需要更换时，请使用节点卡片上的“重生凭证”。</span></div></div>
      </Form>
    </Drawer>
  );
}
