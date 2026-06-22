export type RuntimeConfig = {
  KEYCLOAK_URL?: string;
  KEYCLOAK_REALM?: string;
  KEYCLOAK_CLIENT_ID?: string;
  KEYCLOAK_BACKEND_CLIENT_ID?: string;
  KEYCLOAK_PKCE_METHOD?: string;
};

const runtimeConfig = window.__APP_CONFIG__ || {};

export const appConfig = {
  keycloakUrl: runtimeConfig.KEYCLOAK_URL || import.meta.env.VITE_KEYCLOAK_URL || 'http://localhost:8090',
  keycloakRealm: runtimeConfig.KEYCLOAK_REALM || import.meta.env.VITE_KEYCLOAK_REALM || 'ai-testcase',
  keycloakClientId: runtimeConfig.KEYCLOAK_CLIENT_ID || import.meta.env.VITE_KEYCLOAK_CLIENT_ID || 'frontend',
  keycloakBackendClientId:
    runtimeConfig.KEYCLOAK_BACKEND_CLIENT_ID || import.meta.env.VITE_KEYCLOAK_BACKEND_CLIENT_ID || 'backend',
  keycloakPkceMethod:
    runtimeConfig.KEYCLOAK_PKCE_METHOD || import.meta.env.VITE_KEYCLOAK_PKCE_METHOD || 'S256',
};
