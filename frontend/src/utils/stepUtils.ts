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

  // Detect api_call steps — show description only
  if (step.type === 'api_call' || step.endpoint_ref) {
    return typeof step.description === 'string' ? step.description : '';
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
