# Exceptions reference

This document describes the API error types in [exceptions.py](../../exceptions.py) and how they map to HTTP status codes and JSON responses.

---

## Table of Contents

1. [Overview](#overview)
2. [Base class](#base-class)
3. [Exception classes](#exception-classes)
4. [Usage](#usage)

---

## Overview

All API errors used by the OpenPecha backend inherit from `OpenPechaError`. They are raised by the API and database layers and are handled in [main.py](../../main.py), which returns JSON responses with the appropriate status code.

---

## Base class

**OpenPechaError**

- **Attributes:** `message` (str), `status_code` (int, default 500).
- **Method:** `to_dict() -> dict[str, str]` – returns `{"error": self.message}` for use in JSON responses.

Subclasses override `status_code` only; `to_dict()` is inherited.

---

## Exception classes

| Class | status_code | Typical use |
|-------|-------------|-------------|
| **DataNotFoundError** | 404 | Resource missing (e.g. text, person, edition not found). |
| **InvalidRequestError** | 400 | Bad request (e.g. missing required header, malformed input). |
| **DataConflictError** | 409 | Conflict (e.g. duplicate resource, constraint violation). |
| **DataValidationError** | 422 | Business or database validation failure (e.g. referenced person does not exist, invalid text relationship). |
| **UnauthorizedError** | 401 | Invalid or missing API key, or API key not authorized for the requested application. |

---

## Usage

- **Raising:** Use the appropriate subclass where the failure occurs (e.g. `raise DataNotFoundError("Text not found")`).
- **Handling:** The global exception handler in [main.py](../../main.py) catches `OpenPechaError`, calls `to_dict()`, and returns the result with the exception’s `status_code`. Clients receive a JSON body like `{"error": "Text not found"}` and the corresponding HTTP status.

Pydantic `ValidationError` is handled separately in main (422 with the first validation message); it is not an `OpenPechaError` subclass.
