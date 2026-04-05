# CVDash Security Tests - Implementation Plan

## Status: COMPLETED

All 22 tests implemented and passing.

## Key Understanding from Code Review

### JWT Token Flow (get_current_user in auth.py)
1. Extracts credentials from HTTPBearer (Authorization: Bearer <token>)
2. Decodes JWT with settings.secret_key
3. Extracts user_id from payload
4. Queries DB: `SELECT User WHERE id = user_id`
5. Returns User or raises 401

### Mock DB Setup (conftest.py)
- `mock_db_override` fixture overrides get_db
- Configure with: `mock_db_override.configure_execute(return_value=result)`
- Or set: `result = MagicMock(); result.scalar_one_or_none.return_value = <user>`

### Authentication Failure Paths
- **Expired token**: JWT decode fails -> 401 before DB query
- **Invalid/malformed token**: JWT decode fails -> 401
- **Missing user_id in payload**: extracted user_id is None -> 401
- **Wrong secret**: JWT decode fails -> 401
- **User not in DB**: execute returns None -> 401

### Registration Flow (auth.py /register)
1. Checks email uniqueness: `SELECT User WHERE email = ?`
   - execute().scalar_one_or_none() should return None for no duplicate
2. Creates new User object with email, name, institution, hashed_password
3. db.add(user) + db.commit() + db.refresh(user)
4. refresh() sets id if None (MockAsyncSession.refresh does this)
5. Returns 201 with AuthResponse

### Tests Implemented (22 total)

#### JWT Token Security (5)
1. test_future_expiry_token_accepted: Token valid with future expiry works
2. test_malformed_token_rejected_401: Invalid JWT format rejected
3. test_wrong_secret_token_rejected_401: Token signed with wrong key rejected
4. test_no_user_id_in_token_payload_401: Payload missing user_id rejected
5. test_token_with_null_user_id_401: Token with null user_id rejected

#### Email Validation (2)
1. test_sql_injection_in_email_rejected: SQL injection in email rejected by EmailStr
2. test_invalid_email_format_rejected: Email without @ rejected

#### Password Validation & Hashing (3)
1. test_password_too_short_rejected: Passwords < 8 chars rejected
2. test_same_password_different_hash_each_time: Bcrypt salt creates different hashes
3. test_wrong_password_fails_verification: Wrong password fails verification

#### Authorization Headers (5)
1. test_valid_bearer_format_succeeds_with_valid_user: Valid bearer token with user succeeds
2. test_missing_bearer_prefix_rejected: Token without "Bearer " prefix rejected
3. test_basic_auth_not_supported: Basic auth format rejected
4. test_no_authorization_header_returns_403: Missing auth header rejected
5. test_malformed_bearer_token_rejected: Malformed token after Bearer rejected

#### Health Check & Accessibility (3)
1. test_health_check_accessible_without_auth: Health endpoint no auth needed
2. test_health_check_with_origin_header: Health accepts CORS origin header
3. test_health_check_response_format: Health returns proper JSON structure

#### Malformed Requests (2)
1. test_invalid_json_body_rejected: Invalid JSON rejected
2. test_missing_required_field_rejected: Missing required field rejected

#### Input Types (2)
1. test_wrong_content_type_header_handled: Wrong Content-Type rejected
2. test_empty_json_body_rejected: Empty JSON body rejected

## Implementation Notes

- All JWT security tests use endpoints that need get_current_user (like /api/auth/me)
  - No mock_db_override needed for JWT decode failures (fails before DB query)
  - For user lookup failures, DO override mock_db to return None for user query

- All registration tests use mock_db_override:
  - First execute() returns empty result (no duplicate user)
  - But the Pydantic schema validates email format, catches obvious SQL injection

- For tests requiring valid JWT but no actual user in DB:
  - Use valid token with mock_db configured to return None for user lookup
  - This lets us test: user found but query returns None

- Password hashing tests are standalone, no async needed
- CORS/health are simple GET requests with no auth
