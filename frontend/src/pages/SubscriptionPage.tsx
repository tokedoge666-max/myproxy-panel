import {
  CheckCircleOutlined,
  CopyOutlined,
  EyeOutlined,
  KeyOutlined,
  LinkOutlined,
  QrcodeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { Alert, App, Button, Card, Col, Modal, Row, Segmented, Skeleton, Space, Tag } from 'antd';
import { QRCodeSVG } from 'qrcode.react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { getApiError, subscriptionApi } from '../api/client';
import { PageHeader } from '../components/PageHeader';
import type { SubscriptionInfo } from '../types';

type SubscriptionFormat = 'mihomo' | 'provider';

function normalizedInfo(info: SubscriptionInfo): SubscriptionInfo {
  const baseUrl = info.token
    ? new URL('/sub/' + encodeURIComponent(info.token), window.location.origin).toString()
    : '';
  return {
    ...info,
    mihomo_url: info.mihomo_url || baseUrl,
    provider_url: info.provider_url || (baseUrl ? baseUrl + '?format=provider' : ''),
  };
}

export function SubscriptionPage() {
  const { message, modal } = App.useApp();
  const [info, setInfo] = useState<SubscriptionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewFormat, setPreviewFormat] = useState<SubscriptionFormat>('mihomo');
  const [previewContent, setPreviewContent] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [qrUrl, setQrUrl] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setInfo(normalizedInfo(await subscriptionApi.info()));
      setError('');
    } catch (requestError) {
      setError(getApiError(requestError, '订阅信息读取失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const copy = async (url: string) => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      message.success('订阅地址已复制');
    } catch {
      message.error('复制失败，请手动选择地址');
    }
  };

  const urlFor = useCallback((format: SubscriptionFormat) => {
    if (!info) return '';
    return format === 'mihomo' ? info.mihomo_url : info.provider_url;
  }, [info]);

  const loadPreview = useCallback(async (format: SubscriptionFormat) => {
    const url = urlFor(format);
    if (!url) return;
    setPreviewLoading(true);
    setPreviewContent('');
    try {
      setPreviewContent(await subscriptionApi.preview(url));
    } catch (requestError) {
      message.error(getApiError(requestError, '订阅预览失败'));
    } finally {
      setPreviewLoading(false);
    }
  }, [message, urlFor]);

  const openPreview = (format: SubscriptionFormat) => {
    setPreviewFormat(format);
    setPreviewOpen(true);
    void loadPreview(format);
  };

  const rotate = () => {
    modal.confirm({
      title: '轮换订阅 Token？',
      content: '旧订阅地址会立即失效。完成后需要在所有客户端替换为新地址。',
      okText: '确认轮换',
      cancelText: '取消',
      okButtonProps: { danger: true },
      async onOk() {
        try {
          setInfo(normalizedInfo(await subscriptionApi.rotate()));
          message.success('Token 已轮换，旧地址已失效');
        } catch (requestError) {
          message.error(getApiError(requestError, 'Token 轮换失败'));
          throw requestError;
        }
      },
    });
  };

  const cards = useMemo(() => [
    {
      format: 'mihomo' as const,
      eyebrow: 'FULL PROFILE',
      title: 'Mihomo Subscription',
      description: '包含代理节点、Proxy 选择组与默认 MATCH 规则，可直接导入 Clash Meta 兼容客户端。',
      url: info?.mihomo_url ?? '',
      tag: '推荐',
    },
    {
      format: 'provider' as const,
      eyebrow: 'PROXY PROVIDER',
      title: 'Proxy Provider',
      description: '只输出 proxies 节点列表，适合接入已有 Mihomo 主配置的 proxy-providers。',
      url: info?.provider_url ?? '',
      tag: '高级',
    },
  ], [info]);

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="SECURE DELIVERY"
        title="订阅分发"
        description="安全复制、预览并管理 Mihomo / Clash Meta 兼容订阅。"
        actions={<Space wrap><Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button><Button danger icon={<SyncOutlined />} onClick={rotate} disabled={!info}>轮换 Token</Button></Space>}
      />

      {info?.self_signed_mode ? (
        <Alert type="warning" showIcon message="Development / Self Signed Mode" description="当前使用自签名证书，生成的订阅会启用 skip-cert-verify。正式部署请切换至受信任证书。" />
      ) : null}
      {error ? <Alert type="error" showIcon message="订阅信息读取失败" description={error} /> : null}

      {loading && !info ? (
        <Row gutter={[18, 18]}>{[0, 1].map((item) => <Col xs={24} xl={12} key={item}><Card className="subscription-card"><Skeleton active paragraph={{ rows: 7 }} /></Card></Col>)}</Row>
      ) : (
        <Row gutter={[18, 18]}>
          {cards.map((card) => (
            <Col xs={24} xl={12} key={card.format}>
              <Card className="subscription-card" bordered={false}>
                <div className="subscription-card-head">
                  <div className="subscription-icon"><LinkOutlined /></div>
                  <Tag color={card.format === 'mihomo' ? 'green' : 'blue'} bordered={false}>{card.tag}</Tag>
                </div>
                <span className="card-eyebrow">{card.eyebrow}</span>
                <h2>{card.title}</h2>
                <p>{card.description}</p>
                <div className="url-field">
                  <KeyOutlined />
                  <input value={card.url} readOnly aria-label={card.title + ' 地址'} onFocus={(event) => event.currentTarget.select()} />
                  <Button type="primary" icon={<CopyOutlined />} disabled={!card.url} onClick={() => void copy(card.url)}>复制</Button>
                </div>
                <div className="subscription-actions">
                  <Button icon={<QrcodeOutlined />} disabled={!card.url} onClick={() => setQrUrl(card.url)}>二维码</Button>
                  <Button icon={<EyeOutlined />} disabled={!card.url} onClick={() => openPreview(card.format)}>预览 YAML</Button>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Card className="subscription-security" bordered={false}>
        <div className="subscription-security-icon"><SafetyCertificateOutlined /></div>
        <div><span>ACCESS POLICY</span><h3>订阅地址就是访问密钥</h3><p>请勿公开分享或写入日志。发现泄露时立即轮换 Token，旧链接会同步失效。</p></div>
        <div className="security-checks"><span><CheckCircleOutlined /> Nginx 访问日志关闭</span><span><CheckCircleOutlined /> Token 可即时轮换</span></div>
      </Card>

      <Modal
        title="订阅二维码"
        open={Boolean(qrUrl)}
        onCancel={() => setQrUrl('')}
        footer={<Button onClick={() => setQrUrl('')}>关闭</Button>}
        width={420}
      >
        <div className="qr-modal-content">
          {qrUrl ? <QRCodeSVG value={qrUrl} size={224} level="M" bgColor="#ffffff" fgColor="#071018" marginSize={2} /> : null}
          <p>使用支持二维码导入的客户端扫描。请勿在公共设备上展示。</p>
        </div>
      </Modal>

      <Modal
        title="订阅预览"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={<Button onClick={() => setPreviewOpen(false)}>关闭</Button>}
        width={880}
      >
        <div className="preview-toolbar">
          <Segmented<SubscriptionFormat>
            value={previewFormat}
            options={[{ value: 'mihomo', label: 'Mihomo' }, { value: 'provider', label: 'Provider' }]}
            onChange={(value) => { setPreviewFormat(value); void loadPreview(value); }}
          />
          <span>只读预览 · 敏感内容请勿外传</span>
        </div>
        {previewLoading ? <Skeleton active paragraph={{ rows: 14 }} /> : <pre className="yaml-preview">{previewContent || '# 暂无可预览内容'}</pre>}
      </Modal>
    </div>
  );
}
