import {
  CheckCircleOutlined,
  CloudServerOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
  KeyOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Card,
  Divider,
  Form,
  Input,
  Modal,
  Select,
  Skeleton,
  Space,
  Switch,
} from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { getApiError, settingsApi, singboxApi } from '../api/client';
import { PageHeader } from '../components/PageHeader';
import type { BackupInfo, Settings } from '../types';
import { displayDate, formatBytes } from '../utils/format';

export function SettingsPage() {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm<Settings>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState('');
  const [checkLoading, setCheckLoading] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [selectedBackup, setSelectedBackup] = useState<string>();
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [restoreLoading, setRestoreLoading] = useState(false);
  const selfSigned = Form.useWatch('self_signed_mode', form);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const nextSettings = await settingsApi.get();
      setSettings(nextSettings);
      form.setFieldsValue(nextSettings);
      setError('');
    } catch (requestError) {
      setError(getApiError(requestError, '设置读取失败'));
    } finally {
      setLoading(false);
    }
  }, [form]);

  useEffect(() => { void load(); }, [load]);

  const save = async (values: Settings) => {
    setSaving(true);
    try {
      const updated = await settingsApi.update({ server_name: values.server_name });
      setSettings(updated);
      form.setFieldsValue(updated);
      message.success('设置已保存');
    } catch (requestError) {
      message.error(getApiError(requestError, '设置保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const checkConfig = async () => {
    setCheckLoading(true);
    try {
      const result = await singboxApi.check();
      message.success(result.message || result.detail || 'sing-box 配置校验通过');
    } catch (requestError) {
      message.error(getApiError(requestError, '配置校验失败'));
    } finally {
      setCheckLoading(false);
    }
  };

  const restart = () => {
    modal.confirm({
      title: '重启 sing-box？',
      content: '所有代理连接会短暂中断。重启后系统会自动检查服务状态。',
      okText: '确认重启',
      cancelText: '取消',
      okButtonProps: { danger: true },
      async onOk() {
        try {
          await singboxApi.restart();
          message.success('sing-box 已重启');
        } catch (requestError) {
          message.error(getApiError(requestError, '重启失败'));
          throw requestError;
        }
      },
    });
  };

  const regenerateAll = () => {
    modal.confirm({
      title: '重生所有节点凭证？',
      content: '全部旧节点凭证会立即失效，所有客户端都必须重新更新订阅。此操作不可撤销。',
      okText: '重生全部凭证',
      cancelText: '取消',
      okButtonProps: { danger: true },
      icon: <ExclamationCircleOutlined />,
      async onOk() {
        try {
          await settingsApi.regenerateAllCredentials();
          message.success('所有节点凭证已重新生成');
        } catch (requestError) {
          message.error(getApiError(requestError, '凭证重生失败'));
          throw requestError;
        }
      },
    });
  };

  const openRestore = async () => {
    setRestoreOpen(true);
    setBackupsLoading(true);
    setSelectedBackup(undefined);
    try {
      const list = await singboxApi.backups();
      setBackups(list);
      setSelectedBackup(list[0]?.name);
    } catch (requestError) {
      setBackups([]);
      message.error(getApiError(requestError, '备份列表读取失败'));
    } finally {
      setBackupsLoading(false);
    }
  };

  const restore = async () => {
    if (!selectedBackup) {
      message.warning('请选择一份备份');
      return;
    }
    setRestoreLoading(true);
    try {
      await singboxApi.restore(selectedBackup);
      message.success('备份已恢复，服务状态正常');
      setRestoreOpen(false);
    } catch (requestError) {
      message.error(getApiError(requestError, '备份恢复失败'));
    } finally {
      setRestoreLoading(false);
    }
  };

  return (
    <div className="page-stack settings-page">
      <PageHeader
        eyebrow="CONFIGURATION"
        title="系统设置"
        description="维护面板名称，查看部署身份与 TLS 状态，并执行高风险运维操作。"
        actions={<Space wrap><Button icon={<ExperimentOutlined />} loading={checkLoading} onClick={() => void checkConfig()}>校验配置</Button><Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => form.submit()}>保存设置</Button></Space>}
      />

      {error ? <Alert type="error" showIcon message="设置读取失败" description={error} action={<Button size="small" onClick={() => void load()}>重试</Button>} /> : null}
      {selfSigned ? <Alert type="warning" showIcon message="Development / Self Signed Mode" description="此模式仅适合临时测试，客户端订阅将跳过证书验证。生产环境请配置域名与受信任证书。" /> : null}
      <Alert
        type="info"
        showIcon
        message="网络身份由部署工具管理"
        description="服务器 IP、域名、证书路径和证书模式会同时影响 Nginx、FastAPI 与 sing-box。请通过 sudo bash deploy/reconfigure.sh 修改，避免服务配置不一致。"
      />

      {loading && !settings ? (
        <Card className="settings-card"><Skeleton active paragraph={{ rows: 12 }} /></Card>
      ) : (
        <Form<Settings>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => void save(values)}
          initialValues={{ server_name: 'MyProxy Panel', self_signed_mode: false }}
        >
          <Card className="settings-card" bordered={false}>
            <div className="settings-section-head">
              <div className="settings-section-icon"><CloudServerOutlined /></div>
              <div><span>SERVER IDENTITY</span><h2>服务器信息</h2><p>面板名称可在线修改；网络身份由部署工具统一维护。</p></div>
            </div>
            <Divider />
            <div className="form-grid form-grid--two">
              <Form.Item name="server_name" label="服务器名称" rules={[{ required: true, message: '请输入服务器名称' }, { max: 64 }]}>
                <Input placeholder="MyProxy Panel" />
              </Form.Item>
              <Form.Item
                name="server_ipv4"
                label="服务器 IPv4"
              >
                <Input placeholder="203.0.113.10" inputMode="decimal" disabled />
              </Form.Item>
              <Form.Item
                name="domain"
                label="面板域名"
              >
                <Input placeholder="panel.example.com" disabled />
              </Form.Item>
              <Form.Item name="self_signed_mode" label="自签名开发模式" valuePropName="checked">
                <Switch checkedChildren="启用" unCheckedChildren="关闭" disabled />
              </Form.Item>
            </div>
          </Card>

          <Card className="settings-card" bordered={false}>
            <div className="settings-section-head">
              <div className="settings-section-icon settings-section-icon--blue"><SafetyCertificateOutlined /></div>
              <div><span>TLS CERTIFICATE</span><h2>证书路径</h2><p>Hysteria2、TUIC 与 Web 面板共用这套 TLS 证书。</p></div>
            </div>
            <Divider />
            <div className="form-grid form-grid--two">
              <Form.Item
                name="certificate_path"
                label="证书文件"
              >
                <Input prefix={<SafetyCertificateOutlined />} placeholder="/opt/myproxy/config/tls/fullchain.pem" disabled />
              </Form.Item>
              <Form.Item
                name="private_key_path"
                label="私钥文件"
              >
                <Input prefix={<LockOutlined />} placeholder="/opt/myproxy/config/tls/privkey.pem" disabled />
              </Form.Item>
            </div>
            <div className="tls-note"><CheckCircleOutlined /><span>私钥内容不会经过前端传输；这里只保存服务器上的文件路径。</span></div>
          </Card>
        </Form>
      )}

      <Card className="danger-zone" bordered={false}>
        <div className="danger-zone-head"><span>DANGER ZONE</span><h2>高风险操作</h2><p>每项操作都需要二次确认，并会写入审计日志。</p></div>
        <div className="danger-action-row">
          <div className="danger-action-icon"><ReloadOutlined /></div>
          <div><strong>重启 sing-box</strong><span>短暂断开所有代理连接并重新加载当前配置。</span></div>
          <Button danger onClick={restart}>重启服务</Button>
        </div>
        <div className="danger-action-row">
          <div className="danger-action-icon"><KeyOutlined /></div>
          <div><strong>重生所有凭证</strong><span>使所有节点旧密码、UUID 和密钥立即失效。</span></div>
          <Button danger onClick={regenerateAll}>重生全部凭证</Button>
        </div>
        <div className="danger-action-row">
          <div className="danger-action-icon"><UndoOutlined /></div>
          <div><strong>恢复配置备份</strong><span>选择历史配置，经校验后恢复并重启服务。</span></div>
          <Button danger onClick={() => void openRestore()}>选择备份</Button>
        </div>
      </Card>

      <Modal
        title="恢复 sing-box 配置备份"
        open={restoreOpen}
        onCancel={() => setRestoreOpen(false)}
        onOk={() => void restore()}
        okText="确认恢复"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: restoreLoading, disabled: !selectedBackup }}
      >
        <Alert className="form-alert" type="warning" showIcon message="恢复期间代理连接会短暂中断" description="系统会先校验备份，健康检查失败时保持当前配置不变。" />
        <Select
          className="backup-select"
          loading={backupsLoading}
          value={selectedBackup}
          placeholder={backupsLoading ? '正在读取备份…' : '选择备份'}
          onChange={setSelectedBackup}
          options={backups.map((backup) => ({
            value: backup.name,
            label: backup.name + ' · ' + displayDate(backup.created_at) + ' · ' + formatBytes(backup.size_bytes),
          }))}
          notFoundContent={backupsLoading ? null : '暂无可用备份'}
        />
      </Modal>
    </div>
  );
}
