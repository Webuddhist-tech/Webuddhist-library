class OpenPechaError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": self.message}


class DataNotFoundError(OpenPechaError):
    status_code = 404


class InvalidRequestError(OpenPechaError):
    status_code = 400


class DataConflictError(OpenPechaError):
    status_code = 409


class DataValidationError(OpenPechaError):
    status_code = 422


class ServiceUnavailableError(OpenPechaError):
    status_code = 503


class UnauthorizedError(OpenPechaError):
    status_code = 401


class ForbiddenError(OpenPechaError):
    status_code = 403
