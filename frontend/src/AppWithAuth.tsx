import React, { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, Spin, Result, Button } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import keycloak from './services/keycloak';

// Keycloak 认证状态
interface AuthState {
  initialized: boolean;
  authenticated: boolean;
}

const AppWithAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [authState, setAuthState] = useState<AuthState>({
    initialized: false,
    authenticated: false,
  });

  const initKeycloak = useCallback(async () => {
    try {
      const authenticated = await keycloak.init({
        onLoad: 'login-required',
        checkLoginIframe: false,
        pkceMethod: 'S256',
      });

      setAuthState({ initialized: true, authenticated });

      // 设置 Token 自动刷新
      keycloak.onTokenExpired = () => {
        keycloak
          .updateToken(30)
          .then((refreshed) => {
            if (refreshed) {
              console.log('Token refreshed successfully');
            }
          })
          .catch(() => {
            console.error('Failed to refresh token, redirecting to login');
            keycloak.login();
          });
      };
    } catch (error) {
      console.error('Keycloak initialization failed:', error);
      setAuthState({ initialized: true, authenticated: false });
    }
  }, []);

  useEffect(() => {
    initKeycloak();
  }, [initKeycloak]);

  // 正在初始化
  if (!authState.initialized) {
    return (
      <ConfigProvider locale={zhCN}>
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
          flexDirection: 'column',
          gap: '16px',
        }}>
          <Spin size="large" />
          <span style={{ color: '#666', fontSize: '14px' }}>正在验证身份...</span>
        </div>
      </ConfigProvider>
    );
  }

  // 未认证
  if (!authState.authenticated) {
    return (
      <ConfigProvider locale={zhCN}>
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
        }}>
          <Result
            status="403"
            title="未登录"
            subTitle="请登录后访问系统"
            extra={
              <Button type="primary" onClick={() => keycloak.login()}>
                去登录
              </Button>
            }
          />
        </div>
      </ConfigProvider>
    );
  }

  // 已认证，渲染业务组件
  return <>{children}</>;
};

export default AppWithAuth;
