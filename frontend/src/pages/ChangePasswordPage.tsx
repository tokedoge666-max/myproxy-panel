import { ArrowLeftOutlined, CheckOutlined, KeyOutlined, LogoutOutlined } from '@ant-design/icons';
import { App, Button, Form, Input, Progress } from 'antd';
import { useMemo, useState } from 'react';
import { authApi, getApiError } from '../api/client';
import { passwordChangeRequired, useAuth } from '../auth/AuthContext';
import { useRouter } from '../router/RouterContext';

interface PasswordValues {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

function hasAsciiSpecial(password: string): boolean {
  return Array.from(password).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return (
      (codePoint >= 33 && codePoint <= 47) ||
      (codePoint >= 58 && codePoint <= 64) ||
      (codePoint >= 91 && codePoint <= 96) ||
      (codePoint >= 123 && codePoint <= 126)
    );
  });
}

function passwordScore(password: string): number {
  let score = 0;
  if (password.length >= 12) score += 25;
  if (password.length >= 16) score += 15;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 20;
  if (/\d/.test(password)) score += 20;
  if (hasAsciiSpecial(password)) score += 20;
  return Math.min(100, score);
}

export function ChangePasswordPage() {
  const { user, completePasswordChange, logout } = useAuth();
  const { message } = App.useApp();
  const { navigate } = useRouter();
  const [form] = Form.useForm<PasswordValues>();
  const [submitting, setSubmitting] = useState(false);
  const password = Form.useWatch('newPassword', form) ?? '';
  const score = useMemo(() => passwordScore(password), [password]);
  const forced = passwordChangeRequired(user);

  const submit = async (values: PasswordValues) => {
    setSubmitting(true);
    try {
      await authApi.changePassword(values.currentPassword, values.newPassword);
      await completePasswordChange();
      message.success('密码已更新，请妥善保管');
      navigate('/', { replace: true });
    } catch (error) {
      message.error(getApiError(error, '密码修改失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const leave = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <main className="password-screen">
      <div className="password-card">
        <div className="password-icon"><KeyOutlined /></div>
        <span className="auth-kicker">ACCOUNT SECURITY</span>
        <h1>{forced ? '先设置一个新密码' : '修改管理员密码'}</h1>
        <p>
          {forced
            ? '这是使用临时密码后的首次登录。继续前，请换成只有你知道的高强度密码。'
            : '定期更换管理员密码可以降低凭证泄露风险。'}
        </p>

        <Form<PasswordValues>
          form={form}
          layout="vertical"
          requiredMark={false}
          onFinish={submit}
          size="large"
          className="password-form"
        >
          <Form.Item
            name="currentPassword"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password autoComplete="current-password" placeholder="当前管理员密码" />
          </Form.Item>
          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 12, message: '至少需要 12 个字符' },
              {
                validator: (_, value: string) => {
                  if (!value || (/[A-Z]/.test(value) && /[a-z]/.test(value) && /\d/.test(value) && hasAsciiSpecial(value))) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('需包含大小写字母、数字和特殊字符'));
                },
              },
            ]}
          >
            <Input.Password autoComplete="new-password" placeholder="至少 12 位高强度密码" />
          </Form.Item>
          {password ? (
            <div className="password-strength">
              <span>密码强度</span>
              <Progress
                percent={score}
                showInfo={false}
                strokeColor={score >= 80 ? '#63e6be' : score >= 50 ? '#f3bf63' : '#ff6b7a'}
                trailColor="#1a2731"
                size="small"
              />
            </div>
          ) : null}
          <Form.Item
            name="confirmPassword"
            label="确认新密码"
            dependencies={['newPassword']}
            rules={[
              { required: true, message: '请再次输入新密码' },
              ({ getFieldValue }) => ({
                validator(_, value: string) {
                  if (!value || getFieldValue('newPassword') === value) return Promise.resolve();
                  return Promise.reject(new Error('两次输入的密码不一致'));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" placeholder="再次输入新密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting} className="primary-cta">
            <CheckOutlined /> 保存并进入面板
          </Button>
        </Form>

        <div className="password-actions">
          {!forced ? (
            <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
          ) : <span />}
          <Button type="text" danger icon={<LogoutOutlined />} onClick={() => void leave()}>退出登录</Button>
        </div>
      </div>
    </main>
  );
}
