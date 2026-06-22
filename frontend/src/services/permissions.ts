import keycloak from './keycloak';
import { appConfig } from '../config';

export const Permission = {
  TESTCASE_READ: 'testcase:read',
  TESTCASE_CREATE: 'testcase:create',
  TESTCASE_UPDATE: 'testcase:update',
  TESTCASE_DELETE: 'testcase:delete',
  TESTCASE_GENERATE: 'testcase:generate',
  SESSION_READ: 'session:read',
  SESSION_CREATE: 'session:create',
  SESSION_UPDATE: 'session:update',
  SESSION_DELETE: 'session:delete',
  MODULE_READ: 'module:read',
  MODULE_CREATE: 'module:create',
  MODULE_UPDATE: 'module:update',
  MODULE_DELETE: 'module:delete',
  IOT_READ: 'iot:read',
  IOT_CREATE: 'iot:create',
  IOT_UPDATE: 'iot:update',
  IOT_DELETE: 'iot:delete',
  IOT_EXECUTE: 'iot:execute',
  CONFIG_MANAGE: 'config:manage',
  MCP_MANAGE: 'mcp:manage',
  SKILLS_MANAGE: 'skills:manage',
  GLOBAL_PARAMETER_MANAGE: 'global_parameter:manage',
  SCHEDULED_TASK_MANAGE: 'scheduled_task:manage',
  MOCK_MANAGE: 'mock:manage',
  MOCK_LOG_READ: 'mock_log:read',
  MOCK_LOG_DELETE: 'mock_log:delete',
} as const;

export type PermissionCode = (typeof Permission)[keyof typeof Permission];

const adminPermissions = new Set<PermissionCode>(Object.values(Permission));

const rolePermissions: Record<string, Set<PermissionCode>> = {
  admin: adminPermissions,
  user: new Set<PermissionCode>([
    Permission.TESTCASE_READ,
    Permission.TESTCASE_CREATE,
    Permission.TESTCASE_UPDATE,
    Permission.TESTCASE_DELETE,
    Permission.TESTCASE_GENERATE,
    Permission.SESSION_READ,
    Permission.SESSION_CREATE,
    Permission.SESSION_UPDATE,
    Permission.SESSION_DELETE,
    Permission.MODULE_READ,
    Permission.MODULE_CREATE,
    Permission.MODULE_UPDATE,
    Permission.MODULE_DELETE,
    Permission.IOT_READ,
    Permission.IOT_CREATE,
    Permission.IOT_UPDATE,
    Permission.IOT_DELETE,
    Permission.IOT_EXECUTE,
  ]),
};

export const getCurrentRoles = (): string[] => {
  const parsed = keycloak.tokenParsed as any;
  const backendClientId = appConfig.keycloakBackendClientId;
  const frontendClientId = appConfig.keycloakClientId;
  const realmRoles = parsed?.realm_access?.roles || [];
  const frontendRoles = parsed?.resource_access?.[frontendClientId]?.roles || [];
  const backendRoles = parsed?.resource_access?.[backendClientId]?.roles || [];
  return Array.from(new Set([...realmRoles, ...frontendRoles, ...backendRoles]));
};

export const getCurrentPermissions = (): Set<PermissionCode> => {
  const permissions = new Set<PermissionCode>();
  getCurrentRoles().forEach((role) => {
    if ((Object.values(Permission) as string[]).includes(role)) {
      permissions.add(role as PermissionCode);
    }
    rolePermissions[role]?.forEach((permission) => permissions.add(permission));
  });
  return permissions;
};

export const hasPermission = (permission: PermissionCode): boolean => {
  return getCurrentPermissions().has(permission);
};

export const hasAnyPermission = (permissions: PermissionCode[]): boolean => {
  const currentPermissions = getCurrentPermissions();
  return permissions.some((permission) => currentPermissions.has(permission));
};
