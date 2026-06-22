import React, { useState, useEffect, useCallback } from 'react';
import { ConfigProvider, Spin, Result, Button } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { KeycloakPkceMethod } from 'keycloak-js';
import { appConfig } from './config';
import keycloak from './services/keycloak';

// Keycloak 认证状态
interface AuthState {
  initialized: boolean;
  authenticated: boolean;
}

const getPkceMethod = (): KeycloakPkceMethod => {
  const configured = appConfig.keycloakPkceMethod.toLowerCase();
  if (['false', 'none', 'disabled', 'off'].includes(configured)) {
    return false;
  }

  const hasSha256Digest = typeof window !== 'undefined' && Boolean(window.crypto?.subtle?.digest);
  if (!hasSha256Digest) {
    console.warn('SHA-256 digest is unavailable; Keycloak PKCE has been disabled.');
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

const rotateRight = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits));

const sha256 = (data: Uint8Array) => {
  const hash = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const bitLength = data.length * 8;
  const paddedLength = (((data.length + 9 + 63) >> 6) << 6);
  const padded = new Uint8Array(paddedLength);
  padded.set(data);
  padded[data.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 4, bitLength, false);

  const words = new Array<number>(64);
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = view.getUint32(offset + index * 4, false);
    }
    for (let index = 16; index < 64; index += 1) {
      const s0 = rotateRight(words[index - 15], 7) ^ rotateRight(words[index - 15], 18) ^ (words[index - 15] >>> 3);
      const s1 = rotateRight(words[index - 2], 17) ^ rotateRight(words[index - 2], 19) ^ (words[index - 2] >>> 10);
      words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0;
    }

    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const s1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + s1 + ch + constants[index] + words[index]) >>> 0;
      const s0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (s0 + maj) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }

  const output = new Uint8Array(32);
  const outputView = new DataView(output.buffer);
  hash.forEach((value, index) => outputView.setUint32(index * 4, value, false));
  return output.buffer;
};

const fallbackDigest = async (algorithm: AlgorithmIdentifier, data: BufferSource) => {
  const name = typeof algorithm === 'string' ? algorithm : algorithm.name;
  if (name.toUpperCase() !== 'SHA-256') {
    throw new Error(`Unsupported digest algorithm: ${name}`);
  }
  const bytes = data instanceof ArrayBuffer
    ? new Uint8Array(data)
    : new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  return sha256(bytes);
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

  if (!cryptoSource.subtle?.digest) {
    Object.defineProperty(cryptoSource, 'subtle', {
      configurable: true,
      value: {
        digest: fallbackDigest,
      },
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
