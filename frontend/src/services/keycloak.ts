import Keycloak from 'keycloak-js';

// Keycloak 实例初始化
const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL || 'http://localhost:8080',
  realm: import.meta.env.VITE_KEYCLOAK_REALM || 'ai-testcase',
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID || 'frontend',
});

export default keycloak;
