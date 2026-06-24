/**
 * Step formatting utilities for test case steps.
 * Provides human-readable formatting for both plain-text and structured api_call steps.
 */

export interface ApiCallStepLike {
  type?: string;
  endpoint_ref?: number | string;
  description?: string;
  method?: string;
  path?: string;
  body?: string;
  headers?: Array<{ key: string; value: string }>;
  parameters?: Array<{ key: string; value: string }>;
  variables?: Array<{ key: string; value: string }>;
  assertions?: Array<{ type: string; value?: unknown; jsonpath?: string }>;
}

/**
 * Format a single step (string or api_call object) into human-readable text.
 */
export function formatStep(step: string | Record<string, unknown> | ApiCallStepLike): string {
  if (typeof step === 'string') {
    return step;
  }

  // Detect api_call steps
  if (step.type === 'api_call' || step.endpoint_ref) {
    const ref = step.endpoint_ref ?? '?';
    const desc = step.description ? ` - ${step.description}` : '';
    const method = step.method || '';
    const path = step.path || '';
    const endpointPath = method && path ? ` ${method} ${path}` : '';

    let result = `[API Call] Endpoint #${ref}${endpointPath}${desc}`;

    if (step.headers && Array.isArray(step.headers) && step.headers.length > 0) {
      const h = step.headers.map((h) => `${h.key}: ${h.value}`).join(', ');
      result += `\n  Headers: ${h}`;
    }

    if (step.body) {
      const bodyStr = typeof step.body === 'string' ? step.body : JSON.stringify(step.body);
      // Truncate long body for readability
      const truncated = bodyStr.length > 120 ? bodyStr.substring(0, 120) + '...' : bodyStr;
      result += `\n  Body: ${truncated}`;
    }

    if (step.assertions && Array.isArray(step.assertions) && step.assertions.length > 0) {
      const a = step.assertions
        .map((a) => {
          const parts = [a.type];
          if (a.jsonpath) parts.push(a.jsonpath);
          if (a.value !== undefined && a.value !== null) parts.push(String(a.value));
          return parts.join('=');
        })
        .join(', ');
      result += `\n  Assertions: ${a}`;
    }

    if (step.parameters && Array.isArray(step.parameters) && step.parameters.length > 0) {
      const p = step.parameters.map((p) => `${p.key}=${p.value}`).join(', ');
      result += `\n  Params: ${p}`;
    }

    if (step.variables && Array.isArray(step.variables) && step.variables.length > 0) {
      const v = step.variables.map((v) => `${v.key}=${v.value}`).join(', ');
      result += `\n  Variables: ${v}`;
    }

    return result;
  }

  // Fallback: JSON for other object types
  return JSON.stringify(step, null, 2);
}

/**
 * Format steps array into a single display string (for Excel export, textarea, etc.)
 */
export function formatStepsList(steps: (string | Record<string, unknown>)[]): string {
  return steps
    .map((step, idx) => `${idx + 1}. ${formatStep(step)}`)
    .join('\n');
}
