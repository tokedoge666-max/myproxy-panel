import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntdApp, ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import { AuthProvider } from './auth/AuthContext';
import { RouterProvider } from './router/RouterContext';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#63e6be',
          colorInfo: '#5cc8ff',
          colorSuccess: '#63e6be',
          colorWarning: '#f3bf63',
          colorError: '#ff6b7a',
          colorBgBase: '#080d13',
          colorBgContainer: '#0f171f',
          colorBgElevated: '#121c25',
          colorBorder: '#22313d',
          colorBorderSecondary: '#192631',
          borderRadius: 10,
          borderRadiusLG: 14,
          fontFamily: "Inter, 'SF Pro Display', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
          controlHeight: 40,
          wireframe: false,
        },
        components: {
          Layout: { bodyBg: '#080d13', headerBg: '#0b1118', siderBg: '#0b1118' },
          Menu: { darkItemBg: '#0b1118', darkItemSelectedBg: '#142b2b', darkItemSelectedColor: '#73f0cb', itemBorderRadius: 8 },
          Card: { colorBgContainer: '#0f171f' },
          Table: { headerBg: '#111c25', headerColor: '#91a4b4', rowHoverBg: '#12212a' },
          Modal: { contentBg: '#101922', headerBg: '#101922' },
        },
      }}
    >
      <AntdApp>
        <RouterProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </RouterProvider>
      </AntdApp>
    </ConfigProvider>
  </React.StrictMode>,
);
