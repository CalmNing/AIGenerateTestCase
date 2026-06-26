---
name: api-testing
description: >
  API testing methodology for RESTful/HTTP API test case generation. Use when the user provides API endpoint
  specifications (OpenAPI/Swagger) and asks you to generate test cases that validate API contracts, input
  validation, business logic, authentication, and end-to-end workflows across multiple endpoints.
license: MIT
---

# API Testing

## When to Use

- User provides API endpoint specifications (OpenAPI/Swagger) and asks for test case generation
- Test cases need to validate RESTful API behavior including status codes, response bodies, and error handling
- Multiple endpoints need to be chained together in end-to-end scenario tests
- API input validation, authentication, and business logic need testing coverage

## API Test Design Methodology

You must design API test cases across these dimensions, combining them as appropriate for the specific endpoints under test.

### 1. Contract & Schema Validation

Verify the API conforms to its specified contract:

- **Request Schema**: Validate that the request body matches the specified schema — field types, required fields, nested object structure
- **Response Schema**: Verify response body structure matches spec — expected fields exist, data types are correct
- **HTTP Status Codes**: Verify the correct status code is returned for each scenario (200 OK, 201 Created, 204 No Content, 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 500 Internal Server Error)
- **Response Headers**: Check required headers (Content-Type, Cache-Control, etc.) match specification

**Assertion pattern**: Use `jsonpath_exists` and `jsonpath_equals` to verify response structure and values against the schema.

### 2. Input Validation Testing

Test how the API handles various input conditions:

- **Required Fields**: Omit each required field individually; verify 400/422 status with appropriate error message
- **Field Type Mismatch**: Send wrong types (string for number, object for array, etc.)
- **String Length Boundaries**: Test min/max length, empty string, very long strings
- **Numeric Range Boundaries**: Test min/max values, zero, negative numbers (if applicable)
- **Enum Values**: Test valid enum values and values outside the allowed set
- **Array Constraints**: Test empty arrays, single-element arrays, max items, duplicate items
- **Format Validation**: Test email, URL, date, IP address format valid/invalid inputs
- **Optional Fields**: Verify API works when optional fields are omitted vs provided

**Assertion pattern**: For invalid inputs, assert `status_code_range` 400-499 AND `jsonpath_exists` for error message field.

### 3. Business Logic Testing

Test combinations of fields that produce different business outcomes:

- **Field Combinations**: Different combinations of input fields produce different results (decision table approach)
- **Conditional Logic**: When field A has value X, field B becomes required or behaves differently
- **State Transitions**: Same API call with different system state produces different results
- **Idempotency**: Repeated identical calls produce consistent results (for idempotent endpoints)
- **Concurrency**: Multiple simultaneous operations on the same resource

### 4. Authentication & Authorization Testing

Verify access control works correctly:

- **No Auth Token**: Call endpoint without authentication header; verify 401 response
- **Invalid Token**: Call with malformed or expired token; verify 401/403 response
- **Insufficient Permissions**: Call with valid token that lacks required role/scope; verify 403 response
- **Token Format**: Verify different auth header formats (Bearer, Basic) are handled correctly
- **Endpoint Exposure**: Verify that authenticated-only endpoints are not accessible anonymously

**Assertion pattern**: Auth failures should return 401 (Unauthenticated) or 403 (Forbidden) with consistent error body format.

### 5. CRUD Lifecycle Testing

For endpoints that manage resources, design tests that cover the full resource lifecycle:

- **Create → Read**: Create a resource, then read it back and verify all fields match
- **Create → Update → Read**: Create, update a field, then read and verify the update persisted
- **Create → Delete → Read**: Create, delete, then read and verify 404
- **Create → List**: Create multiple resources, list them, verify pagination and ordering
- **Update Partial**: Send only a subset of fields (PATCH semantics), verify only specified fields changed
- **Update Full**: Send complete resource representation (PUT semantics)

**Variable chaining**: Use `variables` to pass IDs and other state between steps:
- Step 1 creates a resource and extracts its ID via `variables`: `{"key": "resource_id", "value": "$.data.id"}`
- Step 2 uses the extracted ID: `"body": "{\"resourceId\": \"{{resource_id}}\"}"`

### 6. Error Response Testing

Verify error responses are consistent and informative:

- **Error Format**: All error responses should follow the same JSON structure
- **Error Codes**: Business error codes should be documented and consistent
- **Error Messages**: Error messages should be descriptive and user-friendly
- **Field-Level Errors**: Validation errors should indicate which field failed and why
- **Nested Errors**: Complex validation should report all field errors, not stop at the first one

### 7. End-to-End Scenario Testing

Chain multiple API calls to test complete workflows:

- **Happy Path Flow**: Execute the complete user journey from start to finish with valid data
- **Error Recovery**: Start a workflow, hit an error, then recover and complete successfully
- **Data Dependency**: Design test data flow where Step N's output becomes Step N+1's input
- **Cross-Resource**: Test operations that span multiple resource types (e.g., create order with product references)

## Level Mapping for API Tests

| Level | Name | API Testing Focus | Typical Count |
|-------|------|-------------------|---------------|
| 1 (P0) | Functional | Happy path, successful CRUD, valid business scenarios | 1-2 per endpoint |
| 2 (P1/Boundary) | Boundary | Field length/numeric boundaries, pagination limits | 2-3 per endpoint |
| 3 (P2/Exception) | Exception | Invalid input, auth failures, missing fields, error paths | 2-4 per endpoint |
| 4 (P3/Scenario) | Scenario | Multi-step workflows, CRUD cycles, cross-endpoint chains | 1-2 per project |

## Key Rules

1. **Always include assertions**: Every API test case MUST have assertions. At minimum: status_code + one jsonpath check.
2. **Use variables for data chaining**: When step A creates data that step B needs, extract via `variables` and reference via `{{variable_name}}`.
3. **Test both success AND failure**: For every positive scenario, design at least one corresponding negative test.
4. **Verify response body structure**: Don't just check status codes — validate that key fields exist and have expected values.
5. **Test auth separately**: Authentication failures should be tested as a cross-cutting concern across multiple endpoints.
6. **Be specific with parameter values**: Don't use generic placeholders — use business-meaningful values that match the domain.
