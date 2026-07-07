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

export interface TestStepLike {
  step?: number;
  action?: string;
  expected?: string;
}

/**
 * Format a single step (string or api_call object) into human-readable text.
 */
export function formatStep(step: string | Record<string, unknown> | ApiCallStepLike | TestStepLike): string {
  if (typeof step === 'string') {
    return step;
  }

  // Detect TestStep (functional test) — show action field
  if ('action' in step && typeof (step as TestStepLike).action === 'string') {
    const testStep = step as TestStepLike;
    const action = testStep.action?.trim() || '';
    if (action) return action;
  }

  // Detect api_call steps — show description, or fall back to a readable summary
  const stepObj = step as Record<string, unknown>;
  if (stepObj.type === 'api_call' || stepObj.endpoint_ref) {
    const desc = typeof stepObj.description === 'string' ? (stepObj.description as string).trim() : '';
    if (desc) return desc;

    // Fallback: build a human-readable line from available fields
    const parts: string[] = [];
    const method = stepObj.method;
    const path = stepObj.path;
    if (method || path) {
      parts.push([method, path].filter(Boolean).join(' '));
    }
    const name = stepObj.name;
    if (typeof name === 'string' && name) parts.push(name);
    if (parts.length > 0) return parts.join(' — ');

    // Last resort: JSON dump so the step is never visually empty
    return JSON.stringify(step, null, 2);
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
