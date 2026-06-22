import Keycloak from 'keycloak-js';
import { appConfig } from '../config';

// Keycloak 实例初始化
const keycloak = new Keycloak({
  url: appConfig.keycloakUrl,
  realm: appConfig.keycloakRealm,
  clientId: appConfig.keycloakClientId,
});

export default keycloak;
