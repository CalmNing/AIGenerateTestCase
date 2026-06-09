import React, { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, Spin, Result, Button } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { KeycloakPkceMethod } from 'keycloak-js';
import keycloak from './services/keycloak';

// Keycloak 认证状态
interface AuthState {
  initialized: boolean;
  authenticated: boolean;
}

const getPkceMethod = (): KeycloakPkceMethod => {
  const configured = (import.meta.env.VITE_KEYCLOAK_PKCE_METHOD || 'S256').toLowerCase();
  if (['false', 'none', 'disabled', 'off'].includes(configured)) {
    return false;
  }

  const hasWebCrypto = typeof window !== 'undefined' && window.isSecureContext && Boolean(window.crypto?.subtle);
  if (!hasWebCrypto) {
    console.warn('Web Crypto API is unavailable in the current context; Keycloak PKCE has been disabled.');
    return false;
  }

  return 'S256';
};

const fallbackRandomValues = (array: Uint8Array) => {
  for (let index = 0; index < array.length; index += 1) {
    array[index] = Math.floor(Math.random() * 256);
  }
  return array;
};

const fallbackRandomUUID = () => {
  const bytes = new Uint8Array(16);
  const cryptoSource = window.crypto as Crypto | undefined;
  if (cryptoSource?.getRandomValues) {
    cryptoSource.getRandomValues(bytes);
  } else {
    fallbackRandomValues(bytes);
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

const ensureKeycloakCryptoCompatibility = () => {
  if (typeof window === 'undefined') return;

  const cryptoSource = window.crypto || ({} as Crypto);
  if (!window.crypto) {
    Object.defineProperty(window, 'crypto', {
      configurable: true,
      value: cryptoSource,
    });
  }

  if (!cryptoSource.getRandomValues) {
    Object.defineProperty(cryptoSource, 'getRandomValues', {
      configurable: true,
      value: fallbackRandomValues,
    });
  }

  if (!cryptoSource.randomUUID) {
    Object.defineProperty(cryptoSource, 'randomUUID', {
      configurable: true,
      value: fallbackRandomUUID,
    });
  }
};

const AppWithAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [authState, setAuthState] = useState<AuthState>({
    initialized: false,
    authenticated: false,
  });

  const initKeycloak = useCallback(async () => {
    try {
      ensureKeycloakCryptoCompatibility();
      const authenticated = await keycloak.init({
        onLoad: 'login-required',
        checkLoginIframe: false,
        pkceMethod: getPkceMethod(),
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
