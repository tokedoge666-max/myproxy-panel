import { ArrowRightOutlined, LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons';
import { App, Button, Form, Input } from 'antd';
import { useState } from 'react';
import { passwordChangeRequired, useAuth } from '../auth/AuthContext';
import { getApiError } from '../api/client';
import { useRouter } from '../router/RouterContext';

interface LoginValues {
  username: string;
  password: string;
}

export function LoginPage() {
  const { login } = useAuth();
  const { message } = App.useApp();
  const { location, navigate } = useRouter();
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: LoginValues) => {
    setSubmitting(true);
    try {
      const user = await login(values.username.trim(), values.password);
      await message.success('身份验证成功');
      const requested = (location.state as { from?: unknown } | null)?.from;
      const requestedPath = typeof requested === 'string' ? requested : '/';
      navigate(passwordChangeRequired(user) ? '/change-password' : requestedPath, {
        replace: true,
      });
    } catch (error) {
      message.error(getApiError(error, '用户名或密码错误'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-screen">
      <section className="auth-context-panel" aria-label="MyProxy Panel 简介">
        <div className="auth-brand">
          <div className="brand-mark brand-mark--large"><span>MP</span></div>
          <div><strong>MyProxy</strong><span>PERSONAL EDGE CONTROL</span></div>
        </div>
        <div className="auth-context-copy">
          <span className="auth-kicker">PRIVATE CONTROL PLANE</span>
          <h1>你的节点，<br />只由你掌控。</h1>
          <p>统一查看服务状态、维护代理节点并安全分发 Mihomo 订阅。</p>
        </div>
        <div className="auth-trust-row">
          <div><SafetyCertificateOutlined /><span><strong>Secure Cookie</strong><small>会话不写入本地存储</small></span></div>
          <div><LockOutlined /><span><strong>Local API</strong><small>控制面仅通过反向代理访问</small></span></div>
        </div>
      </section>

      <section className="auth-form-panel">
        <div className="login-card">
          <div className="login-status"><span /> SECURE ACCESS</div>
          <h2>管理员登录</h2>
          <p className="login-subtitle">请输入安装时生成的管理员凭证。</p>
          <Form<LoginValues>
            layout="vertical"
            requiredMark={false}
            initialValues={{ username: 'admin' }}
            onFinish={onFinish}
            size="large"
          >
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input prefix={<UserOutlined />} autoComplete="username" placeholder="admin" />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                autoComplete="current-password"
                placeholder="输入管理员密码"
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={submitting}
              className="primary-cta"
            >
              登录控制面板 <ArrowRightOutlined />
            </Button>
          </Form>
          <div className="login-footnote">
            <SafetyCertificateOutlined /> 登录请求通过同源加密会话提交
          </div>
        </div>
      </section>
    </main>
  );
}
