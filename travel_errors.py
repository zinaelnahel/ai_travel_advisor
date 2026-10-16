"""Safe, typed operational errors. Never expose raw provider messages."""


class ServiceError(RuntimeError):
    def __init__(self, code, message, status=502, retryable=False, provider_status=None, provider_request_id=None):
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable
        self.provider_status, self.provider_request_id = provider_status, provider_request_id


class ContextLimitError(ValueError):
    code = 'context_limit'
    status = 422
    retryable = False


def provider_failure(status, error_code=None, request_id=None):
    if status == 401:
        return ServiceError('provider_authentication', 'The AI provider rejected the server API credentials. The app owner needs to check the API key.', 503, provider_status=status, provider_request_id=request_id)
    if status == 403:
        return ServiceError('provider_access', 'The AI provider denied access to the requested model or feature. The app owner needs to check project permissions.', 503, provider_status=status, provider_request_id=request_id)
    if status == 429:
        if error_code in ('insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active'):
            return ServiceError('provider_quota', 'The AI project has no available quota or billing allowance. The app owner needs to check provider billing.', 503, provider_status=status, provider_request_id=request_id)
        return ServiceError('provider_rate_limit', 'The AI provider is receiving requests too quickly. Wait a moment and retry.', 429, True, status, request_id)
    if status in (408, 504):
        return ServiceError('provider_timeout', 'The AI provider took too long to respond. Your message is kept so you can retry.', 504, True, status, request_id)
    if status in (400, 404, 422):
        return ServiceError('provider_request', 'The AI provider could not accept this request or model configuration. The app owner can investigate using the request ID.', 502, False, status, request_id)
    return ServiceError('provider_unavailable', 'The AI provider is temporarily unavailable. Please retry.', 503, True, status, request_id)


def describe_failure(error):
    if isinstance(error, (ServiceError, ContextLimitError)):
        return error
    if isinstance(error, TimeoutError):
        return ServiceError('provider_timeout', 'The request took too long. Your message is kept so you can retry.', 504, True)
    if isinstance(error, (ValueError, KeyError, TypeError, ArithmeticError)):
        return ServiceError('plan_validation', 'The generated response could not be validated. Retry your message; you do not need to change your API key.', 502, True)
    if isinstance(error, RuntimeError):
        return ServiceError('upstream_failure', 'The AI request could not be completed. Please retry; the request ID can help the app owner investigate.', 502, True)
    return ServiceError('internal_error', 'The app could not complete this request. The app owner can investigate using the request ID.', 500)
