import {
  ApiOutlined,
  DashboardOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Button, Drawer, Dropdown, Grid, Layout, Menu, Modal, Space, Tooltip } from 'antd';
import { type ReactNode, useMemo, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { useRouter } from '../router/RouterContext';

const { Header, Sider, Content } = Layout;

const routeMeta: Record<string, { title: string; kicker: string }> = {
  '/': { title: '运行总览', kicker: 'CONTROL PLANE' },
  '/nodes': { title: '代理节点', kicker: 'DATA PLANE' },
  '/subscription': { title: '订阅分发', kicker: 'MIHOMO' },
  '/settings': { title: '系统设置', kicker: 'CONFIGURATION' },
};

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const { location, navigate } = useRouter();
  const screens = Grid.useBreakpoint();
  const compact = !screens.lg;
  const [mobileOpen, setMobileOpen] = useState(false);

  const selectedKey = location.pathname === '/' ? '/' : '/' + location.pathname.split('/')[1];
  const meta = routeMeta[selectedKey] ?? routeMeta['/'];

  const menuItems = useMemo(
    () => [
      { key: '/', icon: <DashboardOutlined />, label: '运行总览' },
      { key: '/nodes', icon: <ApiOutlined />, label: '代理节点' },
      { key: '/subscription', icon: <KeyOutlined />, label: '订阅分发' },
      { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
    ],
    [],
  );

  const navigateFromMenu = ({ key }: { key: string }) => {
    navigate(key);
    setMobileOpen(false);
  };

  const confirmLogout = () => {
    Modal.confirm({
      title: '退出管理面板？',
      content: '退出后需要重新输入管理员凭证。',
      okText: '退出',
      cancelText: '取消',
      okButtonProps: { danger: true },
      async onOk() {
        await logout();
        navigate('/login', { replace: true });
      },
    });
  };

  const navigation = (
    <div className="shell-navigation">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true"><span>MP</span></div>
        <div className="brand-copy">
          <strong>MyProxy</strong>
          <span>PERSONAL EDGE</span>
        </div>
      </div>
      <div className="nav-section-label">管理</div>
      <Menu
        className="main-menu"
        mode="inline"
        theme="dark"
        selectedKeys={[selectedKey]}
        items={menuItems}
        onClick={navigateFromMenu}
      />
      <div className="sider-footer">
        <div className="secure-indicator">
          <span className="secure-dot" />
          <div><strong>Control Plane</strong><span>安全会话已连接</span></div>
        </div>
      </div>
    </div>
  );

  return (
    <Layout className="app-shell">
      {!compact ? <Sider width={248} className="desktop-sider">{navigation}</Sider> : null}
      <Drawer
        placement="left"
        width={276}
        open={compact && mobileOpen}
        onClose={() => setMobileOpen(false)}
        className="mobile-nav-drawer"
        closeIcon={false}
      >
        {navigation}
      </Drawer>

      <Layout className="main-layout">
        <Header className="app-header">
          <div className="header-title-wrap">
            {compact ? (
              <Button
                type="text"
                icon={<MenuOutlined />}
                aria-label="打开导航"
                className="mobile-menu-button"
                onClick={() => setMobileOpen(true)}
              />
            ) : null}
            <div>
              <span className="header-kicker">{meta.kicker}</span>
              <strong className="header-title">{meta.title}</strong>
            </div>
          </div>
          <Space size={10}>
            <Tooltip title="API 通过同源安全会话连接">
              <span className="connection-chip"><span />API CONNECTED</span>
            </Tooltip>
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  { key: 'password', icon: <KeyOutlined />, label: '修改密码' },
                  { type: 'divider' },
                  { key: 'logout', danger: true, icon: <LogoutOutlined />, label: '退出登录' },
                ],
                onClick: ({ key }) => key === 'logout' ? confirmLogout() : navigate('/change-password'),
              }}
            >
              <button type="button" className="account-button" aria-label="管理员菜单">
                <Avatar size={34} icon={<UserOutlined />} />
                {!compact ? <span><strong>{user?.username ?? 'admin'}</strong><small>Administrator</small></span> : null}
              </button>
            </Dropdown>
          </Space>
        </Header>

        <Content className="app-content">
          {children}
          <footer className="app-footer">
            <span>MyProxy Panel v1.0</span>
            <span>Single-server control plane</span>
          </footer>
        </Content>
      </Layout>
    </Layout>
  );
}
